# GSB 任务说明

## 题面

签到月历改版后投诉集中爆发：跨 UTC 零点前后各签到一次，两次都发奖；断签一天后累计天数没有归零；补签把上一轮的最后一天算了进来；补签卡扣了但日历没打勾；第 7 天宝箱连点两次领了两份；第 8 天回到第 1 格，累计天数却被清零；补签未来日期也成功；维护重启后整月记录回档。范围是 gsb/batch47-game-a-04 下的 attendance_reward_calendar.py，类 AttendanceCalendar，负责 UTC 日界、连续天数、周期格位、补签卡与周期宝箱。

公开接口（Python 类型注解）：`AttendanceCalendar(*, cycle_days=7, makeup_limit=2, card_cap=5, history_limit=400, rewards=None, clock=None)`；`check_in(player: str, now=None) -> dict`；`makeup(player: str, day, now=None) -> dict`；`grant_makeup_cards(player: str, count: int) -> dict`；`status(player: str, now=None) -> dict`；`history(player: str, limit: int = 30) -> list`；`rewards_table() -> list`；`reset_player(player: str) -> None`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 AttendanceError(code, detail)。

故障与边界：一、check_in 以 now 换算出的 UTC 日期为准，同一 UTC 日第二次调用返回 status=already_checked、code=ATT_ALREADY_CHECKED，不重复发奖也不增加 check_ins；二、首次签到确定周期锚点，周期序号按锚点到目标日的整天数除以 cycle_days 取整，格位按余数加一，第 8 天（cycle_days=7）为第 1 周期第 1 格；三、连续天数按已签到日期的最大日期向前连续计算，中间断开即从 1 重新计数，补签可以接上断点并让连续天数增加；四、每日奖励取 rewards 中对应格位，构造时奖励条数必须等于 cycle_days，非法配置抛 ATT_BAD_CONFIG；五、一个周期内 cycle_days 天全部签到时发放周期宝箱，每个周期只发一次，重复签到或重复补签不得再发；六、makeup 只接受早于今天、晚于等于锚点且属于当前周期的日期，未来或今天抛 ATT_MAKEUP_FUTURE，锚点之前或上一周期抛 ATT_MAKEUP_OUT_OF_CYCLE，已签到抛 ATT_ALREADY_CHECKED；七、补签先校验周期上限再校验卡：本周期已用次数达到 makeup_limit 抛 ATT_MAKEUP_LIMIT，没有卡抛 ATT_NO_MAKEUP_CARD，成功补签扣一张卡并计入 makeups；八、grant_makeup_cards 数量必须为正整数，累加后超过 card_cap 抛 ATT_CARD_LIMIT，失败不改变卡数；九、status 返回 started、checked_today、streak、cycle_index、position、days_checked、makeup_cards、makeup_left、completed_cycles 与 next_reward，未开始的玩家查询状态不得创建签到锚点；十、history 按日期倒序返回最近记录并遵守 limit，reset_player 清空该玩家全部进度但不影响其他人；十一、snapshot 深拷贝锚点、已签到集合、卡数、补签次数与宝箱领取标记，restore 后连续天数与格位一致，快照之后的签到不出现，结构非法抛 ATT_SNAPSHOT_INVALID 且原状态不变；十二、多线程对同一玩家同一 UTC 日并发签到，只能有一次 checked，其余 already_checked，奖励只发一份。

硬约束：check_in 与 makeup 的校验、写签到集合、发奖、计宝箱在同一把锁内完成，任何一步失败都不能留下已扣卡或半格记录；快照跨进程恢复后连续天数、周期格位与宝箱标记必须一致；每个玩家的签到历史按 history_limit 有界，不允许无限堆积。

验收：`python3 -m pytest -q tests` 的 15 条必须全过；不得改测试、跳过或按固定日期写死；并发签到与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 困难
