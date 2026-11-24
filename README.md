# batch47-game-b-06

## 题面

gsb/batch47-game-b-06 的公会招新流水线在周一早上被同一批申请刷屏：有玩家连点三次申请，审核列表里出现三条同名记录；刚被拒绝的人转头又提交；招募位已经满了，入口还在放人；专员请假两周后回来，过期的申请仍能点通过；重新开启招募后，老成员又被拉进花名册，审批日志对不上账。问题集中在 guild_recruit_pipeline.py 的 GuildRecruitPipeline 类，它负责招募开关、玩家申请、审批、冷却与名额。公开接口：GuildRecruitPipeline(*, daily_apply_limit=3, pending_limit=2, application_ttl=1800, cooldown_seconds=600, batch_limit=20, clock=None)；open_recruitment(guild_id, *, seats, min_level=1, tags=(), closes_at=None) -> dict；close_recruitment(guild_id, *, now=None) -> dict；apply(guild_id, player_id, *, level, note="", now=None) -> dict；withdraw(guild_id, player_id, *, now=None) -> dict；decide(guild_id, player_id, *, reviewer, approve, now=None) -> dict；decide_batch(guild_id, player_ids, *, reviewer, approve, now=None) -> list；pending(guild_id) -> list；roster(guild_id) -> list；is_pending(guild_id, player_id) -> bool；expire(now=None) -> int；snapshot()/restore(state)/stats()/reset_stats()。校验失败统一抛 RecruitError(code, detail)。

故障与边界：一、同一玩家在同一公会已有未过期待审申请时重复提交，抛 RECRUIT_DUPLICATE，不能产生第二条记录；二、已入会玩家再次申请抛 RECRUIT_ALREADY_MEMBER；三、招募未开启或已关闭时申请抛 RECRUIT_CLOSED，关闭时返回被丢弃的待审数量；四、对已开启的公会重复开启抛 RECRUIT_ALREADY_OPEN，关闭后重新开启保留既有成员；五、等级低于门槛抛 RECRUIT_LEVEL_LOW；六、名额按已入会人数计算，满员时申请与审批都抛 RECRUIT_FULL，且该申请不能被吞掉；七、待审申请跨公会按玩家计数，超过 pending_limit 抛 RECRUIT_PENDING_LIMIT；八、每日申请次数按 UTC 日期统计，超过 daily_apply_limit 抛 RECRUIT_DAILY_LIMIT，次日恢复；九、审批拒绝后 cooldown_seconds 内不得再申请，抛 RECRUIT_COOLDOWN，冷却结束那一刻起可以申请；十、撤回后不设冷却，可立即重新申请，重复撤回抛 RECRUIT_NOT_PENDING；十一、申请到达 application_ttl 即失效，审批抛 RECRUIT_EXPIRED 并计入统计，expire 返回本次清理条数；十二、decide_batch 逐条处理并逐条给出 approved/rejected/skipped 与错误码，超过 batch_limit 抛 RECRUIT_BATCH_TOO_LARGE，单条失败不影响后续条目。

硬约束：apply 与 decide 全程持锁，同一玩家八个线程并发申请只能有一条进入待审，四个线程抢两个名额只能通过两条；snapshot 必须深拷贝公会配置、待审、成员、冷却、每日计数和统计，restore 后已拒绝玩家的冷却仍在，快照结构非法抛 RECRUIT_SNAPSHOT_INVALID 且原状态不变；每日计数与冷却只按传入的 now 推进，不依赖系统时钟；待审条目随过期清理，不允许无限堆积。

补充要求：关闭招募时待审记录一并作废，重新开启后旧申请不会复活；撤回不退还当日申请次数，也不产生冷却；批量审批的逐条结果必须与单独调用一致，不能出现前面成功、后面统计缺失的半套记录；空批次直接返回空列表；同一玩家跨公会的待审数量与当日次数是两本独立账，任何拒绝路径都不能只写一半状态；审计人员用同一组时间戳重跑审批，应得到相同名单。

验收：python3 -m pytest -q tests 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 困难
