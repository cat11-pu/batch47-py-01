# 步进认证与会话保障等级策略

## 题面

运维值班台上周连续接到三类工单：财务同事在付款审批页反复被要求重新认证，刚做完硬件密钥验证又提示会话失效；另一个租户把浏览器标签放了一夜，第二天点开审批仍然直接通过；还有客服账号在低权限会话里手改 URL，就进了需要高保障等级的页面；安全审计还指出，同一账号在两地同时开会话时，旧会话的等级不会被下调，升级记录也没有任何计数，事后根本说不清谁在什么等级下操作过。范围是 gsb/batch47-safety-a-08 下的 step_up_assurance_guard.py，类 StepUpAssuranceGuard，负责保障等级、资源策略、会话评估、步进升级、轮换撤销和统计，失败统一抛 StepUpError(code, detail)，code 用下面这些字符串。

公开接口：StepUpAssuranceGuard(*, default_max_age: int = 900, idle_timeout: int = 1800, max_sessions_per_subject: int = 5, clock=None)；define_acr_level(acr: str, *, rank: int) -> dict；register_resource(resource: str, *, required_acr: str, max_age: int | None = None, allowed_amr=None) -> dict；register_session(session_id: str, *, subject: str, acr: str, amr, auth_time: str, now=None) -> dict；evaluate(session_id: str, *, resource: str, now=None) -> dict；step_up(session_id: str, *, acr: str, amr=None, auth_time=None, now=None) -> dict；touch(session_id: str, *, now=None) -> dict；rotate_session(session_id: str, *, now=None) -> dict；revoke_session(session_id: str, *, reason: str = "manual") -> None；list_sessions(subject: str) -> list；purge(now=None) -> int；snapshot() -> dict；restore(state: dict) -> None；stats() -> dict；reset_stats() -> None。

故障与边界：一、define_acr_level 登记 acr 与 rank，rank 必须为正整数，重复登记相同 rank 幂等，rank 不同抛 STEP_CONFLICT，非法等级抛 STEP_INVALID_ACR。二、register_resource 要求 required_acr 已登记，max_age 省略时取默认 900 秒，allowed_amr 传空集合抛 STEP_INVALID_RESOURCE，重复登记同内容幂等，不一致抛 STEP_CONFLICT。三、register_session 校验等级、会话 ID、主体和 amr 非空，auth_time 按 ISO 时间解析并统一到 UTC；重复会话 ID 抛 STEP_SESSION_EXISTS，同一主体活跃会话达到上限抛 STEP_SESSION_LIMIT。四、evaluate 对未知会话抛 STEP_UNKNOWN_SESSION，未知资源抛 STEP_UNKNOWN_RESOURCE，已撤销会话抛 STEP_SESSION_REVOKED，空转超时会话抛 STEP_SESSION_EXPIRED。五、等级不足返回 allowed 为 false、reason 为 acr_too_low，不当异常处理；当前等级足够时继续检查 amr 与认证时效，放行与拒绝都要给出当前等级、要求等级、认证年龄和资源时限，便于审计。六、资源登记 allowed_amr 后，会话 amr 与允许集合无交集返回 reason 为 amr_not_allowed，有交集才放行。七、auth_time 距 now 超过资源 max_age 返回 reason 为 stale，正好等于 max_age 仍放行，被拒绝的请求不得刷新 last_seen。八、放行的 evaluate 刷新 last_seen 但不改 auth_time；会话空转超过 idle_timeout 后 evaluate 与 touch 抛 STEP_SESSION_EXPIRED。九、step_up 只能升不能降，目标 rank 低于当前抛 STEP_DOWNGRADE 且会话保持原样，同级或更高级更新 acr、amr、auth_time 并刷新 last_seen。十、rotate_session 生成新会话 ID 并删除旧 ID，旧 ID 再访问抛 STEP_UNKNOWN_SESSION，主体、等级与 auth_time 保持不变。十一、revoke_session 幂等，撤销后 evaluate、step_up、touch 一律抛 STEP_SESSION_REVOKED；list_sessions 按会话 ID 排序并带 revoked 与 expired 标记。十二、snapshot 覆盖 config、acr_levels、resources、sessions、stats 且为深拷贝，restore 后策略与会话继续生效，非法快照抛 STEP_SNAPSHOT_INVALID 且原状态不变；purge 删除已撤销或空转超时会话并返回条数。

硬约束：register_session、evaluate、step_up、rotate_session 全程持锁，16 个线程同时为同一主体建会话时只能有上限个成功，其余全部 STEP_SESSION_LIMIT；快照跨进程可恢复、为深拷贝，恢复前后互不影响；会话表受 max_sessions_per_subject 与 idle_timeout 约束，purge 必须真正释放额度；stats 固定 evaluated、allowed、denied、stepped_up、revoked、rejected 六个键，所有校验错误都计入 rejected。

验收：python3 -m pytest -q tests 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现，不涉及上游提交。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 困难
