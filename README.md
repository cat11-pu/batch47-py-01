# batch47-safety-a-02

## 题面

客厅电视和机顶盒上的设备码登录这周被安全组叫停：客服收到投诉，说随手猜一个八位码就进了别人的账号；有台电视的轮询脚本每 200 毫秒打一次网关，单客户端把整组实例的 CPU 拉满；同一个设备码批准后被两个进程同时兑换，签出了两张访问令牌；用户明明在手机端点过拒绝，电视端仍然拿到了 token；设备码过期后还能查到批准状态；网关滚动重启，几百台已批准未兑换的设备全部要求重新授权；待授权队列没有上限，压测时内存一路涨到 4GB；运维把用户码抄错一位，拿到的是 500 而不是明确提示。范围是 gsb/batch47-safety-a-02 下的 device_authorization_guard.py，类 DeviceAuthorizationGuard，负责客户端登记、用户码生成与归一化、批准与拒绝、轮询节流以及一次性兑换。

公开接口：`DeviceAuthorizationGuard(*, poll_interval: int = 5, expires_in: int = 600, max_pending: int = 200, clock=None)`；`register_client(client_id: str, *, scopes, verification_uri: str) -> dict`；`start(*, client_id: str, scope: str, device_hint: str | None = None, now=None) -> dict`；`lookup_user_code(user_code: str, now=None) -> dict`；`approve(user_code: str, *, subject: str, now=None) -> dict`；`deny(user_code: str, *, reason: str, now=None) -> dict`；`poll(*, client_id: str, device_code: str, now=None) -> dict`；`cancel(device_code: str) -> bool`；`purge(now=None) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。所有拒绝统一抛 DeviceGrantError(code, detail)，code 取下面列出的字符串。

故障与边界：一、客户端重复登记相同 scopes 集合与 verification_uri 幂等返回，配置有任何差异抛 DEVICE_CLIENT_CONFLICT；未登记客户端调用 start 或 poll 抛 DEVICE_UNKNOWN_CLIENT；请求 scope 超出登记集合抛 DEVICE_SCOPE_NOT_ALLOWED。二、start 返回 device_code、规范化的 user_code、verification_uri、expires_in、interval 与 expires_at，user_code 固定八位、字符取自 BCDFGHJKLMNPQRSTVWXZ、形如 XXXX-XXXX，连续多次 start 不得重复。三、lookup_user_code 对大小写、连字符和首尾空格不敏感，长度不对或含字母表外字符抛 DEVICE_CODE_MALFORMED，格式正确但不存在抛 DEVICE_CODE_UNKNOWN。四、过期判定为 now 大于等于 expires_at，lookup、approve、deny、poll 一律抛 DEVICE_CODE_EXPIRED。五、approve 首次把设备置为 approved 并记录 subject；同一 subject 重复提交幂等成功且 approved 计数不重复累加，换 subject 或已拒绝后再批准抛 DEVICE_ALREADY_DECIDED。六、deny 置为 denied 并记录 reason；拒绝后 approve 抛 DEVICE_ALREADY_DECIDED，poll 抛 DEVICE_ACCESS_DENIED。七、poll 的 device_code 与 client_id 必须对应，client 不符抛 DEVICE_CLIENT_MISMATCH，device_code 不存在抛 DEVICE_CODE_UNKNOWN。八、尚未决定时 poll 返回 status 为 authorization_pending 与当前 interval；距上次 poll 不足 interval 抛 DEVICE_SLOW_DOWN，并把该设备 interval 翻倍、以本次时间为新基准。九、已批准时 poll 首次返回 status 为 approved、subject、token_type 为 Bearer、access_token 与 scope，并把设备标记 redeemed；再次 poll 抛 DEVICE_CODE_ALREADY_USED。十、待处理设备上限 max_pending，写满先清理已过期条目，仍满抛 DEVICE_PENDING_FULL 且 started 不增加。十一、cancel 只对 pending 生效，成功返回 True、重复取消返回 False，已批准或已拒绝抛 DEVICE_ALREADY_DECIDED，取消后 poll 抛 DEVICE_CODE_UNKNOWN。十二、purge 删除已到期设备并返回条数；snapshot 覆盖客户端、设备与统计，restore 后已批准未兑换的设备仍能兑换一次、已兑换状态保留，结构非法抛 DEVICE_SNAPSHOT_INVALID 且原状态不变。

硬约束：start、approve、deny、poll、cancel 全程持锁，八路并发 poll 同一已批准设备只能有一路拿到 access_token，其余全部抛 DEVICE_CODE_ALREADY_USED，redeemed 恰好加一；快照必须是深拷贝，跨进程恢复后不与恢复前的写入共享结构；待授权队列严格受 max_pending 限制，不得无界增长；stats 固定返回 started、approved、denied、redeemed、slow_down 五个整数，reset_stats 只清零计数、不影响在线设备。

验收：`python3 -m pytest -q tests` 的 15 条测试必须全部通过；不得修改测试、跳过或按固定输入写死；并发和重启恢复场景自行验证；只做本地实现，不涉及上游提交。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 困难
- module: device_authorization_guard
- typeName: DeviceAuthorizationGuard
