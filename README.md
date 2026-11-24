# GSB 业务任务

## 题面

日常活跃度改版第一周，运营群里贴出了三张截图：有玩家从战场出来看到进度条已经满 100 点，却把三档宝箱全领了一遍，背包里多出三份奖励；同一批工作室号在同一个来源上反复刷分，两小时把活跃度顶到上限，普通玩家排不进奖励榜；补签脚本在跨零点时运行，把前一天的进度和已领档位一起带到了新一天，第二天上线直接再领一次；玩家按 60、90、30 的顺序点宝箱，客户端没有拦，后台把 90 档也发了；线上还出现过请求重放，同一档宝箱的发放记录写了两条。问题集中在 gsb/batch47-game-a-09 的 daily_activity_chests.py，核心类 ActivityChestTrack，需要按 UTC 自然日重做积分累计、来源封顶和里程碑领取。

公开接口（Python 类型注解）：`ActivityChestTrack(*, milestones=None, source_caps=None, max_points_per_day: int = 100, clock=None)`；`register(player: str, *, now=None) -> dict`；`add_points(player: str, source: str, points: int, *, now=None) -> dict`；`progress(player: str, *, now=None) -> dict`；`claim(player: str, threshold: int, *, now=None, request_id: str | None = None) -> dict`；`rollover(now=None) -> int`；`unclaimed(player: str, *, now=None) -> list`；`day_of(now=None) -> str`；`source_breakdown(player: str, *, now=None) -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 ActivityError(code, detail)，code 用下面的字符串。

故障与边界：一、milestones 缺省为 30、60、90、100，必须严格升序、正整数、不超过 10 档且不超过 max_points_per_day，否则构造即抛 ACT_INVALID；source_caps 的值必须为非负整数，max_points_per_day 必须为正，违反同样抛 ACT_INVALID。二、register 幂等，返回 player、day、points、claimed，day 为 UTC 日期字符串如 2026-11-23；未注册玩家查询或加分抛 ACT_UNKNOWN_PLAYER。三、add_points 只接受 1 到 100 的整数，布尔值视为非法，source 必须非空字符串，否则抛 ACT_INVALID，成功返回 player、day、points、awarded、dropped。四、单次加分先按来源日上限截断，再按每日总上限截断，awarded 与 dropped 之和等于本次请求分值。五、source_breakdown 返回当天各来源实际入账分值，progress 返回 player、day、points、claimed 升序列表以及下一个已达成未领取的 next，没有则为 None。六、claim 的 threshold 必须是配置里的档位，否则抛 ACT_UNKNOWN_THRESHOLD；积分不足抛 ACT_INSUFFICIENT_POINTS；必须按档位从低到高领取，跳过低档抛 ACT_OUT_OF_ORDER；重复领取同一档抛 ACT_ALREADY_CLAIMED；成功返回 player、day、threshold、status 为 claimed。七、带 request_id 的领取必须幂等，重放返回首次结果且不重复计发，同一 request_id 换档位抛 ACT_REQUEST_CONFLICT。八、日期切换以 UTC 零点为准，now 落在新的一天时先结算旧一天：未领取但已达成的档位计入 expired_chests，积分、来源计数、已领档位全部清零，rollovers 加一。九、now 早于玩家当前记账日期抛 ACT_CLOCK_BACKWARDS，不允许回拨时间刷分。十、rollover 返回本次重置的玩家数，未到新一天的玩家不计入。十一、unclaimed 只列已达成但未领取的档位，按升序返回，跨天后清空。十二、snapshot 返回 version 为 1、包含 players 与 stats 的深拷贝，players 下每个玩家含 day、points、claimed、sources，stats 返回 awarded、dropped、claimed、expired_chests、rollovers 五个计数，restore 遇到结构或数值非法抛 ACT_SNAPSHOT_INVALID 且原状态不变，reset_stats 全部归零。

硬约束：加分、领取与跨天重置在同一把锁内完成，多线程并发加分时每日总上限必须精确，不能因为竞争多记账；同一档位并发领取只能有一个成功，其余抛 ACT_ALREADY_CLAIMED；每次操作只处理单个玩家，跨天清理按玩家推进，不允许每次调用全量重算所有玩家。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与快照恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段（不进入题面）

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 困难
