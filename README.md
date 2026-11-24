# 刷新令牌家族轮换与重用检测

## 题面

客服系统从上周起不断有用户投诉：同一台手机在弱网下重试刷新，页面直接掉登录；安全团队复盘时发现，被窃取的旧刷新令牌在三十秒后再次使用仍然换到了新令牌；还有租户反馈退出登录后，另一台设备上的旧令牌照样能续期。范围是 gsb/batch47-safety-a-06 下的 refresh_token_rotation_guard.py，类 RefreshRotationGuard，负责客户端注册、授权签发、刷新轮换、家族撤销与统计，失败统一抛 RefreshError(code, detail)，code 为下列字符串。

公开接口：RefreshRotationGuard(*, grace_seconds: int = 30, max_chain: int = 50, clock=None)；register_client(client_id: str, *, scopes, refresh_ttl: int = 3600, grace_seconds: int | None = None) -> dict；issue(client_id: str, *, subject: str, scopes, now=None) -> dict；rotate(refresh_token: str, *, client_id: str, scopes=None, now=None) -> dict；revoke_token(refresh_token: str, *, reason: str = "manual") -> None；revoke_family(family_id: str, *, reason: str = "manual") -> int；inspect(refresh_token: str) -> dict；family_tokens(family_id: str) -> list；purge(now=None) -> int；snapshot() -> dict；restore(state: dict) -> None；stats() -> dict；reset_stats() -> None。

故障与边界：一、首次签发返回 generation 0、family_id、按字典序去重的 scopes，expires_at 为 now 加 refresh_ttl；同一客户端重复注册且配置相同时幂等返回，任一字段不同抛 RT_CLIENT_CONFLICT，空 scopes 抛 RT_INVALID_CLIENT。二、rotate 成功把旧令牌置为 consumed 并发放 generation 加一的新令牌，新旧令牌值必须不同；旧令牌再次出现且距上次消费小于 grace_seconds 时返回同一个后继令牌并置 grace_hit 为 true，超过宽限则判为重用。三、判定重用后整条家族立即失效并抛 RT_REUSE_DETECTED，之后家族内任何活跃令牌抛 RT_FAMILY_REVOKED，已消费令牌状态保持 consumed。四、令牌不存在抛 RT_UNKNOWN；令牌归属客户端与传入 client_id 不一致抛 RT_CLIENT_MISMATCH，且不得消耗该令牌。五、请求 scopes 必须是当前令牌 scopes 的子集，越权抛 RT_SCOPE_ESCALATION；缩权后的令牌只能继续缩权，不能恢复原有权限。六、到期按精确时刻比较，now 大于等于 expires_at 即抛 RT_EXPIRED；purge 删除到期令牌并返回条数，被清理的令牌再 inspect 抛 RT_UNKNOWN。七、代际上限 max_chain，generation 加一超过上限抛 RT_CHAIN_LIMIT 并同时撤销家族，防止无限轮换。八、issue 遇到未注册客户端抛 RT_UNKNOWN_CLIENT，scope 超出客户端登记范围抛 RT_SCOPE_NOT_ALLOWED，subject 为空抛 RT_INVALID_SUBJECT。九、revoke_token 只撤销单个令牌，重复调用幂等且不重复计数，未知令牌抛 RT_UNKNOWN；revoke_family 返回本次由活跃转撤销的令牌数，重复调用返回 0，未知家族抛 RT_UNKNOWN_FAMILY。十、family_tokens 按 generation 升序返回 token、generation、status、scopes、expires_at，未知家族抛 RT_UNKNOWN_FAMILY。十一、stats 固定五个键 rotated、grace_hits、reused、revoked、rejected，rejected 只统计 issue 与 rotate 的校验失败，重用只记 reused，显式撤销只累加 revoked，reset_stats 归零。十二、snapshot 覆盖 config、clients、families、tokens、stats 且为深拷贝，restore 后已消费令牌仍按重用处理，结构非法抛 RT_SNAPSHOT_INVALID 且原状态完全不变。

硬约束：rotate、revoke 与快照全程持锁，多线程用同一令牌并发轮换只能有一个成功，其余全部按重用处理并触发家族撤销，计数与实际状态必须一致；快照跨进程可恢复、为深拷贝，恢复前后互不影响；家族与令牌数量受 max_chain、refresh_ttl 和 purge 约束，不允许无界增长，清理只按到期时间。

验收：python3 -m pytest -q tests 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现，不涉及上游提交。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 地狱
