# GSB 任务包

## 题面

客服侧收到一类泄露工单：用户在公司门户里打开第三方客服聊天页，页面里的脚本在用户不知情时连上了消息网关，把当前登录人的历史会话全拉走了。复盘发现 WebSocket 握手几乎不设防：Origin 缺失直接放行，Origin: null 也放行；子域策略写成了后缀比较，攻击者拿 evil-app.example.com 就混了进来；app.example.com.attacker.net 这类后缀相似域名被判成同源；票据只查存在不查状态，同一张握手票据被重放二十多次，每次都新建会话；票据签发时绑定的来源和握手时提交的来源不一致照样通过；单来源并发连接没有上限，一个页面开了三千条连接把网关内存拖垮；扩容重启后已用票据全部回到未使用状态，脚本立刻又来一轮。

范围是 gsb/batch47-safety-d-09 下的 websocket_origin_guard.py，类型 OriginGuard，负责来源策略登记、握手票据签发、握手校验、并发配额、会话关闭与快照恢复。公开接口（Python 类型注解）：`OriginGuard(*, max_connections: int = 500, ticket_ttl: int = 60, max_per_origin: int = 50, clock=None)`；`register_origin(origin: str, *, allow_subdomains: bool = False, max_per_origin: int | None = None) -> dict`；`revoke_origin(origin: str) -> None`；`normalize_origin(raw: str) -> str`；`issue_ticket(origin: str, *, subject: str, ttl: int | None = None, now=None) -> dict`；`handshake(headers, *, peer: str, now=None) -> dict`；`close(ticket: str) -> dict`；`ticket_state(ticket: str) -> dict`；`origin_status(origin: str) -> dict`；`expire(now=None) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 OriginError(code, detail)。握手成功返回 status、ticket、origin、subject、peer 五个字段；issue_ticket 返回 ticket、origin、subject、expires_at，到期时刻按 UTC ISO-8601 输出；close 返回 ticket、state、freed；ticket_state 返回 ticket、origin、subject、state、accepted_at；origin_status 返回 origin、allow_subdomains、max_per_origin、active、revoked；stats 固定 issued、accepted、rejected、closed、expired 五个计数。

故障与边界：一、来源重复登记但 allow_subdomains 或 max_per_origin 任一不同抛 ORIGIN_CONFLICT，完全相同幂等返回，无法规范化的输入抛 ORIGIN_INVALID；二、normalize_origin 统一小写主机、去掉 https 的 443 与 http 的 80 默认端口、允许末尾单个斜杠，带路径、查询、用户信息、ws 或 wss 协议、空主机、非数字端口一律抛 ORIGIN_INVALID，null 抛 ORIGIN_NULL；三、撤销未登记来源抛 ORIGIN_UNKNOWN，重复撤销抛 ORIGIN_REVOKED，撤销时该来源的活跃会话全部关闭并让出配额，撤销后再登记、签票、撤销或握手一律抛 ORIGIN_REVOKED；四、签票时来源未登记抛 ORIGIN_UNKNOWN，subject 为空或 ttl 非正整数抛 ORIGIN_INVALID，票据编号按签发顺序唯一且不重复；五、握手要求 Upgrade 含 websocket、Connection 含 upgrade，否则抛 WS_BAD_UPGRADE；Sec-WebSocket-Version 不等于 13 抛 WS_BAD_VERSION；Sec-WebSocket-Key 不是 16 字节的 Base64 抛 WS_BAD_KEY；六、Origin 缺失抛 WS_ORIGIN_MISSING，值为 null 抛 WS_ORIGIN_NULL，无法规范化抛 WS_ORIGIN_INVALID，这些判定全部先于票据校验；七、来源必须命中登记的策略，scheme 与端口必须一致，未命中抛 WS_ORIGIN_DENIED，允许子域的策略只匹配同名主机或多级子域，app.example.com.attacker.net、evil-app.example.com、notcorp.example.org 都不算命中；八、票据缺失抛 WS_TICKET_MISSING，未知抛 WS_TICKET_UNKNOWN，与握手来源不一致抛 WS_TICKET_ORIGIN_MISMATCH，已使用、已关闭或已取消的票据再握手抛 WS_TICKET_USED，这些拒绝都不消耗票据；九、now 大于等于票据到期时刻抛 WS_TICKET_EXPIRED，恰好等于算过期，expire 只把到期未用的票据置为 expired 并返回条数，同一时刻重复调用返回 0；十、单来源活跃会话达到该策略 max_per_origin 抛 WS_ORIGIN_FULL，全局活跃达到 max_connections 抛 WS_CAPACITY，配额拒绝不消耗票据，关闭会话后可继续用原票据完成握手；十一、close 对未使用票据返回 cancelled 且 freed 为 false，对活跃会话返回 closed 并释放一个配额，对已结束票据抛 WS_TICKET_CLOSED，未知票据抛 WS_TICKET_UNKNOWN；十二、snapshot 深拷贝策略、撤销名单、票据、会话与统计，键为 version、policies、revoked、tickets、sessions、stats，restore 后活跃配额、已用票据与统计原样恢复，结构非法或版本不符抛 WS_SNAPSHOT_INVALID 且原状态不变。

硬约束：issue_ticket、handshake、close、expire、revoke_origin 全程持同一把锁，八线程用同一张票据同时握手只能有一个 accepted，其余全部 WS_TICKET_USED，活跃会话计数不得被穿透；配额判定必须排在票据消费之前，任何被拒绝的握手都不改变票据状态与活跃数；票据与会话表受 expire 与配额约束，不得无界增长；normalize_origin、ticket_state、origin_status 只读，不得改变状态或触发过期；票据首次转为 expired 时计一次 expired，stats 精确区分五个计数，reset_stats 全部归零。

验收：`python3 -m pytest -q tests` 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 困难
- name: batch47-safety-d-09
- module: websocket_origin_guard
- typeName: OriginGuard
