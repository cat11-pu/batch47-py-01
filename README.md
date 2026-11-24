# GSB 任务

## 题面

发布平台把制品按 dev、staging、prod 三个环境逐级晋级，每次晋级要两名审批人，生产窗口还要避开财务结账的冻结时段。上个月的事故是：一个已经在 dev 被替换掉的旧摘要，因为 staging 的票据还挂着，被直接推到了 prod；同一张票据被两个流水线同时晋级，审计里出现两条部署记录；审批人超过时限没有处理，票据过期后仍能批准；冻结窗口内提交的晋级被放过；值班人回滚时把环境指针清空，只能手工恢复；有人用同一个制品反复开票，一小时里刷了三十次部署。

范围是 gsb/batch47-safety-b-05 下的 release_promotion_guard.py，类 PromotionGuard，负责环境登记、审批授权、冻结窗口、晋级票据与回滚。公开接口：PromotionGuard(*, ticket_ttl: int = 3600, approval_ttl: int = 900, max_tickets: int = 256, max_promotions_per_hour: int = 10)；register_environment(name: str, *, rank: int, required_approvals: int = 1, allowed_approvers=None) -> dict；authorize_approver(approver_id: str, *, environments) -> dict；freeze_environment(name: str, *, start, end, reason: str) -> dict；clear_freezes(name: str) -> int；open_ticket(artifact_digest: str, *, target_env: str, requested_by: str, now=None) -> dict；approve(ticket_id: str, *, approver: str, now=None) -> dict；promote(ticket_id: str, *, now=None) -> dict；rollback_environment(name: str, *, actor: str, now=None) -> dict；deployment(name: str) -> dict | None；ticket(ticket_id: str) -> dict；list_tickets() -> list；snapshot() -> dict；restore(state: dict) -> None；stats() -> dict；reset_stats() -> None。失败统一抛 PromotionError(code, detail)。

故障与边界：一、环境 rank 必须唯一且为正整数，重复等级抛 PROMO_RANK_CONFLICT，同名环境配置不同抛 PROMO_CONFLICT，审批人数小于 1 抛 PROMO_INVALID_QUORUM；二、审批人授权覆盖的环境必须已登记，重复授权配置不同抛 PROMO_CONFLICT，未授权的审批人抛 PROMO_APPROVER_UNAUTHORIZED，环境可以再收窄到 allowed_approvers 名单；三、制品摘要只接受 64 位小写十六进制，否则 PROMO_INVALID_DIGEST，票据数量达到 max_tickets 抛 PROMO_CAPACITY_FULL；四、除最低等级外，开票时前一级环境的当前部署摘要必须与制品一致，否则 PROMO_CHAIN_MISMATCH，晋级时再校验一次；五、申请人不能给自己审批，否则 PROMO_SELF_APPROVAL，重复审批幂等返回 already=True 且不重复计数；六、达到 required_approvals 后票据转为 approved，之后任何人再批准都返回 already=True 且不再追加；七、票据截止时间取整到秒，now 大于等于截止时间时 approve 与 promote 都抛 PROMO_TICKET_EXPIRED；八、promote 要求票据处于 approved，否则 PROMO_NOT_APPROVED；已晋级的票据再次调用返回 already=True，不重复写部署记录；九、冻结窗口按左闭右开判断，窗口内 promote 抛 PROMO_FROZEN，窗口互相重叠抛 PROMO_FREEZE_OVERLAP，起止颠倒抛 PROMO_INVALID_WINDOW，clear_freezes 返回清理条数；十、审批记录超过 approval_ttl 在晋级时失效，剩余人数不足则票据退回 pending 并抛 PROMO_APPROVAL_STALE；十一、同一环境一小时内的成功晋级次数达到 max_promotions_per_hour 抛 PROMO_RATE_LIMITED；十二、rollback 只能回到该环境上一份部署，历史不足两条抛 PROMO_NO_HISTORY，回滚后 deployment 与历史同时回退；snapshot 覆盖环境、审批人、冻结窗口、票据、部署历史与速率记录，结构非法抛 PROMO_SNAPSHOT_INVALID 且状态不变。

硬约束：approve 与 promote 全程持锁，多个线程同时审批同一票据时最终只记录恰好 required_approvals 条批准并只发生一次状态迁移；多个流水线同时晋级同一票据时只有一个真正写入部署记录，其余返回 already=True；票据、部署历史与速率窗口都受配置上限约束，不能因重复提交无界增长；stats 区分 opened、approved、promoted、rolled_back、rejected，reset_stats 归零。

验收：python3 -m pytest -q tests 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发审批与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 困难
- module: release_promotion_guard
- typeName: PromotionGuard
