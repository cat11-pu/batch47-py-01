# batch47-safety-a-03

## 题面

校统一身份认证平台刚把 CAS 服务从旧门户迁到新集群，故障单当天就堆了一百多张：同一个 ST 被浏览器并发刷新两次，两次都换到了用户属性；service 参数被改成邻校地址，校验接口照样返回成功；未登记的第三方系统也能拿到票据；管理员注销 TGT 之后，同一会话还能继续签发新票据和代理票据；代理链没有深度限制，业务方套了五层还在放行；票据存储没有上限，压测时内存一路涨；集群重启后，昨天已经消费过的 ST 重新生效；不可续期的 TGT 也能签出 renew 票据；跨时区节点上时间窗口差一个小时，刚签发的票据被判过期；下游服务顺手改了返回的属性字典，第二张票据的属性跟着变了。范围是 gsb/batch47-safety-a-03 下的 cas_ticket_guard.py，类 CasTicketGuard，负责服务登记、TGT 与 ST 签发、代理票据链、一次性校验和票据存储。

公开接口：`CasTicketGuard(*, ticket_ttl: int = 120, tgt_ttl: int = 7200, max_tickets: int = 1000, max_proxy_depth: int = 2, clock=None)`；`register_service(service_id: str, *, url_pattern: str, allow_proxy: bool = False) -> dict`；`issue_tgt(*, principal: str, attributes=None, renewable: bool = True, now=None) -> dict`；`issue_service_ticket(tgt: str, *, service: str, renew: bool = False, now=None) -> dict`；`validate_service_ticket(ticket: str, *, service: str, now=None) -> dict`；`issue_proxy_granting_ticket(tgt: str, *, service: str, now=None) -> dict`；`issue_proxy_ticket(grant: str, *, target_service: str, now=None) -> dict`；`validate_proxy_ticket(ticket: str, *, target_service: str, now=None) -> dict`；`destroy_tgt(tgt: str) -> bool`；`purge(now=None) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。所有拒绝统一抛 CasError(code, detail)，code 取下面列出的字符串。

故障与边界：一、register_service 支持精确 URL 与以 /* 结尾的前缀模式，重复登记相同配置幂等返回，配置不同抛 CAS_SERVICE_CONFLICT；未登记或与任何模式都不匹配的 service 抛 CAS_UNKNOWN_SERVICE。二、issue_tgt 返回 tgt、principal、attributes、expires_at、renewable，有效期按 tgt_ttl 秒计算，返回的 attributes 与内部状态互不影响。三、issue_service_ticket 遇到不存在的 TGT 抛 CAS_TGT_UNKNOWN、now 大于等于 TGT 到期时间抛 CAS_TGT_EXPIRED；renew 为真而 TGT 不可续期抛 CAS_RENEW_NOT_ALLOWED；票据有效期按 ticket_ttl 秒计算，返回 ticket、service、renew、expires_at。四、validate_service_ticket 对不存在的票据、把 TGT 或 PT 当 ST 传入一律抛 CAS_TICKET_UNKNOWN；已消费抛 CAS_TICKET_ALREADY_USED；now 大于等于票据到期时间抛 CAS_TICKET_EXPIRED；service 与签发时不一致抛 CAS_SERVICE_MISMATCH 且不消费票据；成功后把票据置为已消费并返回 status 为 ok、principal、attributes 的深拷贝、renew 与空的 proxies 列表。五、TGT 过期或被销毁不影响此前已签发 ST 的校验；TGT 有效期内可以重复签发多张 ST。六、destroy_tgt 删除 TGT 及其名下全部 PGT 并返回 True，TGT 不存在返回 False；此后签发 ST 抛 CAS_TGT_UNKNOWN，使用其 PGT 抛 CAS_PROXY_GRANT_UNKNOWN。七、issue_proxy_granting_ticket 要求目标 service 登记时 allow_proxy 为真，否则抛 CAS_PROXY_NOT_ALLOWED；返回 pgt、pgt_iou、service、expires_at，其 depth 为 1。八、issue_proxy_ticket 的 grant 可以是 PGT，也可以是尚未消费的 PT，target_service 必须登记匹配；新票据 depth 为来源 depth 加一，代理链追加来源 service，depth 超过 max_proxy_depth 抛 CAS_PROXY_CHAIN_TOO_DEEP。九、validate_proxy_ticket 的未知、已消费、过期与 target 不符判定和 ST 一致，成功时额外返回从最早到最近的 proxies 链。十、存储上限 max_tickets 统计存活的 TGT 与各类票据，写满先清理过期条目，仍满抛 CAS_STORE_FULL 且不创建任何票据。十一、purge 删除过期 TGT、ST、PGT 与 PT 并返回删除条数，过期判定同样为 now 大于等于 expires_at。十二、snapshot 覆盖服务登记、TGT、票据与统计，restore 后服务模式仍可匹配、已消费票据保持已消费、被销毁的 TGT 仍不可用，结构非法抛 CAS_SNAPSHOT_INVALID 且原状态不变。

硬约束：所有签发、校验、销毁、清理操作全程持锁，八路并发校验同一 ST 只能有一路成功，其余全部抛 CAS_TICKET_ALREADY_USED；快照必须是深拷贝，跨进程恢复后不与恢复前的写入共享可变结构；票据存储受 max_tickets 约束，禁止无界增长；stats 固定返回 tgt_issued、st_issued、validated、rejected、proxy_issued 五个整数，其中 proxy_issued 统计 PGT 与 PT 的签发总数，reset_stats 归零。

验收：`python3 -m pytest -q tests` 的 15 条测试必须全部通过；不得修改测试、跳过或按固定输入写死；并发和重启恢复场景自行验证；只做本地实现，不涉及上游提交。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 地狱
- module: cas_ticket_guard
- typeName: CasTicketGuard
