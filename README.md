# 转码集群槽位调度

## 题面

转码集群扩容到二十个节点后，调度开始失控：同一部片子被两个租户同时提交，跑了两遍；高优先级任务排在低优先级后面；某租户一次占了全部槽位，其他租户排队几小时；worker 崩溃后任务永远卡在运行中，没人回收；运维把租户配额调小，新任务仍然被接进来；批量提交中间一条参数错误，前面几条已经进队列，回滚不了；重启调度器后待跑任务和运行中租约全部丢失。范围是 gsb/batch47-game-h-04 下的 transcode_scheduler.py，类 TranscodeScheduler，负责任务提交、优先级派发、租户配额、租约回收、抢占与快照恢复。

公开接口：`TranscodeScheduler(*, total_slots=8, per_tenant_slots=4, queue_limit=100, lease_ms=30000, batch_limit=50, max_attempts=3)`；`submit(job, *, tenant, priority=0) -> dict`；`submit_batch(jobs, *, tenant, priority=0) -> list`；`acquire(*, worker, now_ms) -> dict | None`；`complete(job_id, *, worker, now_ms) -> dict`；`fail(job_id, *, worker, now_ms, retry=False) -> dict`；`preempt(*, tenant, count, now_ms) -> list`；`reap(now_ms) -> list`；`set_quota(tenant, *, slots) -> dict`；`pending() -> list`；`running() -> list`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 TxError(code, detail)。

故障与边界：任务必须有非空 id 和大于零的 duration_ms，否则抛 TX_JOB_INVALID；id 在待跑、运行中或近期完成记录里出现过抛 TX_DUPLICATE_JOB；待跑数达到 queue_limit 抛 TX_QUEUE_FULL，批量超过 batch_limit 抛 TX_BATCH_TOO_LARGE；submit_batch 先整批校验任务、批内重复和剩余容量，任一条不合格就整批拒绝，一条都不入队；acquire 按优先级从高到低、同优先级按提交先后取任务，跳过已达租户配额的任务，总运行数达到 total_slots 或无任务可派时返回 None，租约到期时间等于 now_ms 加 lease_ms；complete 遇到未知租约抛 TX_LEASE_UNKNOWN，worker 不匹配抛 TX_LEASE_MISMATCH，now_ms 到达或超过到期时间抛 TX_LEASE_EXPIRED；fail 在 retry 为真且 attempts 未达 max_attempts 时重排入队并返回 retried，达到上限返回 dead，不重试则返回 failed；preempt 按优先级从低到高、同级按租约到期从晚到早回收指定租户的运行任务并重新排队，count 不大于零抛 TX_PREEMPT_INVALID；reap 回收已到期租约，未达上限重排并记为 expired，达到上限记为 dead，返回按 job_id 排序的结果；set_quota 只接受 0 到 total_slots 的整数，否则抛 TX_QUOTA_INVALID；pending 与 running 返回稳定排序的副本；snapshot 顶层为 version/pending/running/stats，其中 pending 与 running 都是列表，restore 恢复队列顺序、租约、配额与统计，非法结构抛 TX_SNAPSHOT_INVALID 且原状态不变；stats 含 submitted、started、completed、failed、retried、dead、preempted、expired 八项。

pending 每项给出 job_id、tenant、priority、attempts 并按派发顺序排列，running 每项给出 job_id、tenant、worker、priority、lease_expires 并按 job_id 排序；acquire 成功返回 job_id、tenant、priority、attempts、worker、lease_expires 六个字段；complete 成功返回 job_id、state 与 duration_ms，fail 返回 job_id、state 与 attempts；reap 每项含 job_id、outcome、attempts；已完成任务的 id 留在近期历史里，重复提交仍被拒。

硬约束：submit、acquire、complete 与 reap 共用同一把锁，十个线程同时抢三个槽位只能发出三份租约，其余返回 None；同一 job_id 只能存在一份租约，重复提交必须被拒；队列长度、批大小、历史记录与租约时长都有硬上限，任何失败提交不得改变队列；快照可跨进程恢复，恢复后继续 complete 或 acquire 仍遵守原租约和配额。

验收：`python3 -m pytest -q tests` 的 15 条测试必须全部通过；不得改测试、跳过或按固定输入写死；并发与恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- difficulty: 地狱
