# GSB 任务包

## 题面

上周五下午开始，合作银行的单点登录后台陆续收到“退出登录不生效”的投诉：用户在 IdP 点了退出，回到业务系统刷新页面仍然能继续操作。翻日志发现后通道登出请求其实都到了网关，但校验几乎什么都没拦住——没有 events 声明的令牌照样放行；同一个 jti 连续推送两次，两次都返回成功；iss 指向别家 IdP 的令牌也进了登出流程；sub 和 sid 都为空的令牌被当成有效请求；签名密钥轮换后旧指纹签发的令牌仍在通过；iat 写成未来时间、exp 写成过去时间的令牌各有一例被接受。更麻烦的是网关滚动重启，重启前处理过的 jti 全部丢失，攻击者拿旧令牌能把刚建立的会话再登出一次，审计里分不清是重放还是新请求；jti 缓存没有上限，被灌了三万多条后内存冲到 2G。

范围是 gsb/batch47-safety-a-07 下的 oidc_backchannel_logout_guard.py，类型 BackchannelLogoutGuard，负责 OIDC 后通道登出令牌的校验、jti 去重和会话撤销。公开接口：`BackchannelLogoutGuard(*, clock_skew: int = 60, max_jti: int = 500, session_capacity: int = 100, batch_limit: int = 20, clock=None)`；`register_client(client_id: str, *, issuer: str, audiences, logout_endpoint: str | None = None) -> dict`；`trust_issuer(issuer: str, *, fingerprints, allowed_algs=None) -> dict`；`revoke_issuer(issuer: str) -> None`；`start_session(session_id: str, *, client_id: str, subject: str, sid: str | None = None) -> dict`；`validate_logout_token(token: dict, *, client_id: str, now=None) -> dict`；`apply_logout(token: dict, *, client_id: str, now=None) -> dict`；`apply_logout_batch(tokens, *, client_id: str, now=None) -> list`；`is_session_active(session_id: str) -> bool`；`session_status(session_id: str) -> dict`；`purge(now=None) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 OidcLogoutError(code, detail)，code 取下面列出的字符串。

故障与边界：一、同一 jti 第二次走 apply_logout 返回 {"status": "replayed", "code": "OIDC_LOGOUT_REPLAY"}，不抛异常，已登出的会话保持登出且不重复计数；二、client 未注册抛 OIDC_UNKNOWN_CLIENT，同 client_id 重复注册但 issuer、受众或登出地址不同抛 OIDC_CLIENT_CONFLICT，完全一致幂等返回；三、签发方未登记抛 OIDC_UNTRUSTED_ISSUER，已撤销抛 OIDC_ISSUER_REVOKED，撤销未登记签发方抛 OIDC_UNKNOWN_ISSUER；四、token 的 iss 必须等于该 client 登记的 issuer，否则抛 OIDC_ISSUER_MISMATCH；五、aud 可以是字符串或列表，必须包含 client_id，否则抛 OIDC_AUDIENCE_MISMATCH；六、alg 必须在签发方允许算法集合内，否则抛 OIDC_ALG_NOT_ALLOWED；七、fingerprint 不在签发方信任指纹集合抛 OIDC_SIGNATURE_UNTRUSTED；八、events 必须是对象且包含 backchannel-logout 事件、事件值为对象，缺失或形状不对抛 OIDC_BAD_EVENTS，令牌带非空 nonce 抛 OIDC_NONCE_PRESENT；九、iss、aud、iat、exp、jti、alg 缺失或类型不符、exp 不大于 iat 抛 OIDC_MALFORMED_TOKEN，sub 与 sid 均为空抛 OIDC_SUBJECT_REQUIRED；十、时间按 UTC 比较，now 早于 iat 减 clock_skew 抛 OIDC_NOT_YET_VALID，now 大于等于 exp 加 clock_skew 抛 OIDC_TOKEN_EXPIRED，正好落在边界算过期；十一、带 sid 时找不到该 client 名下的会话抛 OIDC_SID_UNKNOWN，token 带 sub 且与会话 subject 不符抛 OIDC_SUBJECT_MISMATCH，只带 sub 时撤销该 subject 在此 client 下全部活跃会话并按 session_id 升序返回；十二、jti 缓存上限 max_jti，满时先清理 exp 加 clock_skew 已过的条目，仍满抛 OIDC_JTI_FULL 且不撤销会话、不记 jti；过期但未清理的 jti 仍按重放处理，purge 返回删除条数。

硬约束：apply_logout 全程持锁，多线程同时提交同一 jti 只能有一个 accepted，其余全部 replayed；快照必须深拷贝，覆盖 client、签发方、撤销名单、会话、jti 缓存和统计，restore 遇到结构非法输入抛 OIDC_SNAPSHOT_INVALID 且原状态不变，恢复后已消费的 jti 仍按重放处理；批量接口逐条处理并逐条给出 accepted、replayed、rejected 与 index，超过 batch_limit 在处理任何一条之前抛 OIDC_BATCH_TOO_LARGE，单条失败不影响后续；会话容量按活跃数计算，先做同名会话的幂等或冲突判定再判容量，满抛 OIDC_SESSION_FULL；stats 固定包含 accepted、replayed、rejected、sessions_revoked 四个计数，reset_stats 全部归零。

验收：`python3 -m pytest -q tests` 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 困难
- name: batch47-safety-a-07
- module: oidc_backchannel_logout_guard
- typeName: BackchannelLogoutGuard
