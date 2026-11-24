# 任务说明

## 题面

团本战斗组这周连续收到状态结算对不上的工单：同一个灼烧被两名法师先后挂上，层数没有增加，到期时间也没刷新；战士开着盾墙仍被眩晕，免疫条目像是没生效；两个同组怒吼增益同时挂在身上，属性直接翻倍；驱散把不可驱散的誓约一并清掉，清除顺序还和剩余时间无关；持续伤害在有效期结束后继续跳，一次长暂停后几十条 tick 同时刷出；目标栏在 buff 到期后仍显示图标。同一次战斗里，驱散和周期伤害的先后顺序也会影响结果：先驱散再结算和先结算再驱散，玩家血量能差出一截，而现有记录没有把这一刻的顺序保留下来。测试同学用两份只差一个到期时间的日志都无法得到相同结果。更麻烦的是战斗日志回放和线上结算各算一套，同一份状态快照恢复后层数与到期时间全变，值班同学无法复现问题。

范围是 gsb/batch47-game-c-01 下的 status_effect_resolver.py，类 StatusEffectResolver，负责效果定义、目标注册、层数叠加、驱散、周期 tick 与快照。公开接口：`__init__(*, tick_limit: int = 10, max_targets: int = 1000, clock=None)`；`define_effect(effect_id: str, *, name: str = "", category: str = "buff", max_stacks: int = 1, duration_ms: int = 1000, stack_policy: str = "refresh", exclusive_group=None, tick_interval_ms: int = 0, dispellable: bool = True) -> dict`；`remove_effect_definition(effect_id: str) -> bool`；`register_target(target_id: str, *, max_effects: int = 8, immunity=()) -> dict`；`unregister_target(target_id: str) -> bool`；`apply_effect(target_id: str, effect_id: str, *, now_ms: int, source: str = "", power: int = 1) -> dict`；`remove_effect(target_id: str, effect_id: str, *, now_ms=None, reason: str = "manual") -> dict`；`dispel(target_id: str, *, category=None, limit=None, now_ms: int) -> list`；`tick(target_id: str, *, now_ms: int) -> list`；`active_effects(target_id: str, *, now_ms: int) -> list`；`remaining_ms(target_id: str, effect_id: str, *, now_ms: int) -> int`；`purge(now_ms: int) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 EffectError(code, detail)，code 为字符串。

故障与边界：一、effect_id 相同且字段完全一致的重复定义幂等返回 created=False，任一字段不同抛 EFFECT_DEFINITION_CONFLICT；二、目标重复注册同理，max_effects 或 immunity 不同抛 EFFECT_TARGET_CONFLICT，目标数超过 max_targets 抛 EFFECT_TARGET_LIMIT；三、refresh 策略重复施加只保留一层并把到期时间推到 now+duration_ms，返回 refreshed；四、stack 策略依次返回 applied、stacked、capped，层数封顶 max_stacks，每次施加都刷新到期时间；五、活跃效果数达到 max_effects 时新增抛 EFFECT_TARGET_FULL，刷新已有层不受限，已过期条目不计入容量；六、immunity 按 effect_id 或 category 命中，返回 status=immune 且不写入任何状态；七、exclusive_group 相同的另一效果被替换，返回值带 replaced 列表，统计 replaced 加一；八、dispel 只清 dispellable 条目，可按 category 过滤，按 expires_at_ms、effect_id 升序，limit 截断，统计记 dispelled；九、tick 只为 next_tick_ms 到期的效果生成事件，同一时刻按 effect_id 升序，过期事件排在所有 tick 后，等于或晚于到期时间的 tick 不生成；十、单次 tick 的待发事件数超过 tick_limit 时抛 EFFECT_TICK_BURST，且不推进任何 next_tick_ms、不删条目；十一、purge 删除 expires_at_ms <= now_ms 的条目并返回条数；十二、snapshot 返回 version=1 的深拷贝，覆盖 definitions、targets 与 stats；restore 遇到类型错误、版本不符、负数计数抛 EFFECT_SNAPSHOT_INVALID 且原状态完全不变，恢复后统计一并还原。

硬约束：apply_effect、tick、dispel、purge 必须整段持锁，八个线程同时给同一目标叠同一个 stack 效果时最终层数必须正好等于 max_stacks，不能超发；快照必须与内部对象完全隔离，恢复前后继续写入互不影响；效果数与待发 tick 都受上限约束，禁止无界增长。验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或按固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 地狱
