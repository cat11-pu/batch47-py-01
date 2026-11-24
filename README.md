# batch47-safety-a-05

## 题面

支付网关把用户令牌换成下游服务令牌的这套 exchange 服务，上线后接连出现越权：运营只申请 read，下游却拿到了 write；同一个 subject token 被三个服务同时提交，三次都换到了新令牌；某服务拿自己的 actor token 换到了目标系统的 admin scope；代理链套了七八层也没有任何限制；令牌明明已经过期，exchange 仍返回新令牌；网关重启后，昨天用过的 jti 全部复活，攻击者拿旧令牌又换了一次；签发方白名单之外的目标系统也能通过；把 actor 令牌当 subject 令牌提交没有报错；jti 台账没有上限，压测时内存翻了一倍；两个进程同时兑换同一令牌，两边都成功，审计里出现两条不同的派生令牌。范围是 gsb/batch47-safety-a-05 下的 token_exchange_delegation.py，类 DelegationExchangeGuard，负责签发方、actor 与资源登记、主体令牌和 actor 令牌签发、逐跳收窄兑换、jti 台账和委托链审计。

公开接口：`DelegationExchangeGuard(*, max_delegation_depth: int = 3, max_jti: int = 1000, ttl: int = 300, clock=None)`；`register_issuer(issuer: str, *, audiences, scopes) -> dict`；`register_actor(actor_id: str, *, audiences, scopes) -> dict`；`register_resource(audience: str, *, scopes) -> dict`；`issue_subject_token(*, issuer: str, subject: str, audience: str, scopes, jti=None, not_before=None, expires_at=None, now=None) -> dict`；`issue_actor_token(actor_id: str, *, audience: str, scopes, jti=None, not_before=None, expires_at=None, now=None) -> dict`；`exchange(*, subject_token: dict, actor_token: dict | None = None, audience: str, scopes, now=None) -> dict`；`introspect(token: dict, *, now=None) -> dict`；`revoke(jti: str) -> None`；`purge(now=None) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。所有拒绝统一抛 ExchangeError(code, detail)，code 取下面列出的字符串。

故障与边界：一、register_issuer、register_actor、register_resource 重复相同配置幂等返回并把 audiences、scopes 按字典序输出，同一标识配置有任何差异抛 EXCHANGE_REGISTRATION_CONFLICT。二、issue_subject_token 要求签发方已登记、audience 在签发方允许集合内、scopes 非空且是签发方 scope 子集，否则分别抛 EXCHANGE_UNTRUSTED_ISSUER、EXCHANGE_AUDIENCE_NOT_ALLOWED、EXCHANGE_SCOPE_EMPTY、EXCHANGE_SCOPE_ESCALATION；返回 token_type 为 subject，含 issuer、subject、audience、排序后的 scopes、iat、nbf、exp、jti、depth 为 0 与空的 act。三、issue_actor_token 要求 actor 已登记、audience 在 actor 允许集合内、scopes 非空且不超过 actor scope 上限，未知 actor 抛 EXCHANGE_ACTOR_INVALID，audience 不符抛 EXCHANGE_ACTOR_AUDIENCE_MISMATCH。四、exchange 的 subject_token 只接受 subject 或 access，actor_token 只接受 actor，形状或类型不符抛 EXCHANGE_TOKEN_TYPE_INVALID。五、exchange 时间判定为 now 大于等于 exp 抛 EXCHANGE_TOKEN_EXPIRED、now 小于 nbf 抛 EXCHANGE_TOKEN_NOT_YET_VALID，actor 令牌超时或尚未生效统一抛 EXCHANGE_ACTOR_INVALID。六、请求 scopes 非空、必须是 subject 令牌 scopes 的子集、目标资源 scope 的子集，存在 actor 时还必须是 actor scopes 的子集，否则抛 EXCHANGE_SCOPE_EMPTY 或 EXCHANGE_SCOPE_ESCALATION。七、目标 audience 必须在签发方允许集合内并登记为资源，否则抛 EXCHANGE_AUDIENCE_NOT_ALLOWED；actor 令牌的 audience 必须等于本次目标 audience，否则抛 EXCHANGE_ACTOR_AUDIENCE_MISMATCH。八、每跳 depth 加一，超过 max_delegation_depth 抛 EXCHANGE_DEPTH_EXCEEDED 且不消费 jti；有 actor 时 act 记录最近 actor 与从最早到最近的 chain，无 actor 时沿用上游 act。九、subject 令牌的 jti 只能消费一次，重复提交抛 EXCHANGE_TOKEN_REPLAYED 且不产生新令牌；被撤销的 jti 抛 EXCHANGE_TOKEN_REVOKED，同时命中时撤销优先。十、jti 台账上限 max_jti，写满先清理到期条目，仍满抛 EXCHANGE_JTI_FULL 且不消费当前 jti；revoke 幂等且只对第一次撤销计数。十一、派生令牌 exp 取 now 加 ttl 与 subject exp 的较小值，expires_in 为剩余整秒，scope 为排序后空格连接，access_token 以 at_ 开头且 jti 与上游不同。十二、introspect 返回 active 以及 token_type、subject 或 actor、audience、scopes、depth、expires_in，失效时 active 为 false 且 reason 取 expired、not_yet_valid、replayed、revoked 之一，不改状态不计数；purge 清理到期 jti 返回条数；snapshot 覆盖登记表、jti 台账、撤销名单与统计，restore 后已用 jti 仍按重放处理、撤销仍生效，结构非法抛 EXCHANGE_SNAPSHOT_INVALID 且原状态不变。

硬约束：exchange 与登记、撤销、清理全程持锁，八路并发提交同一 subject token 只能有一路成功，其余全部抛 EXCHANGE_TOKEN_REPLAYED；快照必须是深拷贝，跨进程恢复后不与恢复前的写入共享可变结构；jti 台账严格受 max_jti 约束，禁止无界增长；stats 固定返回 exchanged、rejected、replayed、revoked 四个整数，其中 replayed 是 rejected 中因重放失败的部分，reset_stats 归零。

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
- module: token_exchange_delegation
- typeName: DelegationExchangeGuard
