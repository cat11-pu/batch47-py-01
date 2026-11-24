# batch47-game-b-07

## 题面

gsb/batch47-game-b-07 的公会任务板在版本更新后连续出事故：同一个精英任务被两名成员同时认领，两人都点完成后奖励发了两次；限时任务过了截止时间仍显示可接，接完才发现进度不再增长；有成员一口气认领五个任务占位，真正想打的人接不到；前置任务没做完的人也能认领后续任务，整条任务链在结算时断掉。问题集中在 guild_quest_board.py 的 GuildQuestBoard 类，负责任务发布、认领名额、进度上报、结算与过期清理。公开接口：GuildQuestBoard(*, max_open=20, max_active_per_member=2, claim_ttl=1800, clock=None)；publish(guild_id, quest_id, *, title, slots=1, target=1, reward=100, min_level=1, requires=(), expires_at=None, now=None) -> dict；claim(guild_id, quest_id, *, player_id, level=1, now=None) -> dict；release(guild_id, quest_id, *, player_id, now=None) -> dict；report_progress(guild_id, quest_id, *, player_id, amount=1, now=None) -> dict；complete(guild_id, quest_id, *, player_id, now=None) -> dict；cancel(guild_id, quest_id, *, reason="", now=None) -> dict；claims(guild_id, quest_id=None) -> list；quest(guild_id, quest_id) -> dict；member_summary(guild_id, player_id) -> dict；expire(now=None) -> int；snapshot()/restore(state)/stats()/reset_stats()。校验失败统一抛 QuestError(code, detail)。

故障与边界：一、同一公会内任务编号重复抛 QUEST_DUPLICATE，字段非法（名额或目标为零、奖励为负、标题为空、截止时间不晚于当前）抛 QUEST_BAD_SPEC；二、依赖的任务不存在抛 QUEST_UNKNOWN_REQUIRES，依赖自身抛 QUEST_BAD_SPEC；三、未登记的公会或任务抛 QUEST_UNKNOWN_GUILD、QUEST_UNKNOWN_QUEST；四、等级低于 min_level 抛 QUEST_LEVEL_LOW；五、名额按已完成人数加在认人数计算，占满抛 QUEST_FULL，释放后名额立刻回到池子；六、同一玩家重复认领同一任务抛 QUEST_ALREADY_CLAIMED，不额外占名额；七、跨任务统计的在认数量达到 max_active_per_member 抛 QUEST_MEMBER_LIMIT；八、前置任务未由该玩家完成时认领抛 QUEST_REQUIRES_UNMET；九、进度只能增加且封顶到 target，超出部分返回 clamped 为真，数量小于一抛 QUEST_BAD_AMOUNT；十、进度未满时结算抛 QUEST_INCOMPLETE，结算成功后同一玩家再次结算抛 QUEST_ALREADY_DONE，奖励和统计只记一次；十一、认领超过 claim_ttl 后在结算或上报时抛 QUEST_CLAIM_EXPIRED 并释放名额，任务截止时间到期则整单变为 expired；十二、cancel 丢弃全部在认记录并返回数量，之后认领与结算都抛 QUEST_CLOSED，expire 返回清理掉的认领条数。

硬约束：claim 与 complete 全程持锁，八个线程抢最后一个名额只能成功一个，八个线程同时结算同一份认领只能发放一次奖励；snapshot 深拷贝任务、认领、成员账本与统计，restore 后未结奖励不能被重复领取，非法结构抛 QUEST_SNAPSHOT_INVALID 且原状态不变；max_open 限制每个公会的开放任务数，认领与任务截止只按传入的 now 判断；成员奖励累计与活动认领数必须能从 member_summary 精确复算。

补充要求：任务编号一经发布即长期占用，取消或过期后也不能用同一编号重新发布；释放未认领的任务抛 QUEST_NOT_CLAIMANT，不影响其他成员的名额；进度上报只能由认领人发起；取消的任务不计入成员已完成与奖励累计；任务截止时间早于当前时间直接拒绝，不进入任务板；空任务板查询返回空列表，开放任务数上限在并发发布时同样生效。

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
