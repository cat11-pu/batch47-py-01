# 任务说明

红队演练从一台被拿下的构建机上通过 SSRF 打到了实例元数据接口，拿到的角色凭据有效期长达十二小时，还能被同一台机器上的任意进程复用；换一台机器拿旧 token 也能换到票，跳数限制写在文档里却从未生效。运维侧同样混乱：单角色并发会话没有封顶，续期没有窗口和次数限制，凭据被泄露后只能等自然过期，控制器重启后吊销名单全部丢失；构建机镜像里还缓存着上一次的 token 和到期时间，容器重建后直接拿来复用。请修 gsb/batch47-safety-b-10 下的 imds_credential_broker.py，公开类型 ImdsCredentialBroker，异常 ImdsError(code, detail)，只改这个文件，不要动 tests 目录。

保持这些公开接口：`ImdsCredentialBroker(*, token_ttl=21600, renew_window=900, max_renewals=3, clock=None)`；`register_role(role, *, arn, max_sessions=10, session_ttl=3600, max_ttl=43200) -> dict`；`register_node(node, *, hop_limit=1) -> dict`；`allow_role(node, role) -> dict`；`rotate_token(node, *, now=None) -> dict`；`issue_session(role, *, node, token, hops, ttl=None, now=None) -> dict`；`renew_session(session_id, *, ttl=None, now=None) -> dict`；`revoke_session(session_id, *, reason) -> dict`；`validate(session_id, *, now=None) -> dict`；`sweep(*, now=None) -> int`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。

故障与边界：一、角色登记要求 arn 以 arn:aws:iam:: 开头，max_sessions 限 1 到 100，session_ttl 与 max_ttl 在 60 到 43200 秒之间且 max_ttl 不小于 session_ttl，否则抛 IMDS_BAD_ROLE 或 IMDS_BAD_ARN，同角色同配置幂等，配置变化抛 IMDS_CONFLICT；二、节点 hop_limit 限 1 到 3，重复登记不同值抛 IMDS_CONFLICT，越界抛 IMDS_BAD_HOP_LIMIT；三、节点只能为已授权的角色换票，未授权抛 IMDS_ROLE_NOT_ALLOWED，未知节点或角色分别抛 IMDS_UNKNOWN_NODE、IMDS_UNKNOWN_ROLE；四、rotate_token 使旧 token 立即失效，携带旧 token 或别的节点的 token 抛 IMDS_TOKEN_INVALID，token 有效期从签发时刻起算、与节点登记时间无关，时间达到 expires_at 抛 IMDS_TOKEN_EXPIRED；五、请求跳数必须是 1 到 hop_limit 之间的整数，否则抛 IMDS_HOP_LIMIT；六、ttl 缺省用角色 session_ttl，超过 max_ttl 截断，非正整数抛 IMDS_BAD_TTL；七、单一角色未吊销且未过期的会话数达到 max_sessions 时抛 IMDS_SESSION_LIMIT，已过期或已吊销的会话不占额度；八、续期只在剩余寿命不超过 renew_window 秒时允许，过早抛 IMDS_RENEW_TOO_EARLY，超过 max_renewals 次抛 IMDS_RENEW_LIMIT，续期后的到期时间不得晚于签发时间加 max_ttl，达到该上限抛 IMDS_TTL_EXCEEDED；九、已过期会话续期抛 IMDS_SESSION_EXPIRED，已吊销会话续期抛 IMDS_SESSION_REVOKED；十、revoke_session 后 validate 返回 revoked 并带原因，重复吊销幂等，空原因抛 IMDS_BAD_REASON；十一、validate 对活跃、过期、吊销分别返回 active、expired、revoked，active 时带向下取整的剩余秒数，未知会话抛 IMDS_UNKNOWN_SESSION；十二、sweep 删除已过期的会话记录并返回条数，删除后 validate 抛 IMDS_UNKNOWN_SESSION，snapshot 覆盖 version、roles、nodes、sessions、stats、token_sequence、session_sequence，必须深拷贝，restore 结构非法抛 IMDS_SNAPSHOT_INVALID 且原状态不变。

硬约束：换票的 token 校验、额度检查、会话写入在同一把锁内完成，八个请求并发抢三个名额只能成功三个；会话 ID 不允许重复或复用；sweep 与会话额度检查共享同一份状态，清理过程中不能出现额度短暂翻倍；快照恢复到新进程后仍要能验证吊销状态、剩余寿命和续期次数；每角色会话数、token 与续期次数都有硬上限。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复要自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 困难
