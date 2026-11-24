# 任务说明

## 题面

团本三号首领的转阶段最近成了事故高发点：输出组把血量从 100% 一口气打到 25%，首领只切到二阶段，三阶段的技能整场没出现；有时候又相反，血线只到 61% 却提前进了二阶段，团队被迫重打。转阶段瞬间旧阶段技能还能放，新阶段技能却要等一轮默认冷却；同一帧里两个队员同时按下触发，首领连放两次全屏技能，直接团灭。软狂暴计时按真实时间算，跨天维护后计时器归零，硬狂暴该来的时候没有来；首领血量归零后仍能继续承伤，死亡结算与狂暴状态互相覆盖。服务器重启后阶段、技能冷却和狂暴时间全部丢失，回放里的阶段序列和线上不一致。战斗日志还缺少转阶段记录，复盘时说不清是第几秒进的第几阶段。

范围是 gsb/batch47-game-c-05 下的 boss_phase_controller.py，类 BossPhaseController，负责阶段定义、战斗推进、技能门禁、狂暴计时与快照。公开接口：`__init__(*, ability_cooldown_ms: int = 5000, soft_enrage_ms: int = 600000, hard_enrage_ms: int = 900000, max_events: int = 500)`；`define_phases(phases) -> dict`；`engage(*, now_ms: int, max_hp: int) -> dict`；`damage(amount: int, *, now_ms: int) -> dict`；`set_hp(hp: int, *, now_ms: int) -> dict`；`cast(ability_id: str, *, now_ms: int) -> dict`；`available_abilities(*, now_ms: int) -> list`；`status(*, now_ms: int) -> dict`；`reset_encounter(*, now_ms: int) -> dict`；`history(limit: int = 20) -> list`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 PhaseError(code, detail)。

故障与边界：一、阶段列表必须从 1 开始连续编号，首档血量阈值为 100，后续严格递减且不小于 1，技能 ID 全局唯一，空列表、阈值不降、编号跳号、空技能名、负冷却都抛 PHASE_BAD_DEFINITION；二、相同阶段定义重复提交幂等返回 created=False，内容不同抛 PHASE_CONFLICT，进入战斗后再改阶段抛 PHASE_LOCKED；三、未定义阶段就开战或查询状态抛 PHASE_NOT_READY，重复开战抛 PHASE_ALREADY_ENGAGED，最大血量非正抛 PHASE_BAD_AMOUNT；四、扣血后按结算血量判断，一次跨过多档时每跨一档记一条 transitions，hp_percent 用结算后的百分比，阶段只前进不后退；五、进入新阶段时该阶段技能立即就绪，旧阶段技能变为不可用，施放旧阶段技能抛 PHASE_ABILITY_LOCKED；六、技能冷却优先取该技能自己的 cooldown_ms，否则用构造函数默认值，now_ms 小于 ready_at_ms 抛 PHASE_COOLDOWN 并在 detail 给出 ability_id 与 ready_at_ms，等于 ready_at_ms 允许施放；七、未知技能抛 PHASE_UNKNOWN_ABILITY，available_abilities 只返回当前阶段且已就绪的技能 ID 并按字典序；八、从开战时刻起算，经过 soft_enrage_ms 进入 soft，经过 hard_enrage_ms 进入 hard，边界时刻即生效，status 返回 elapsed_ms、enrage、hp_percent、phase、casts；九、血量归零后 state 变 dead、lethal=True，再扣血或施法抛 PHASE_ENCOUNTER_OVER，status 仍可查询；十、set_hp 高于当前血量抛 PHASE_HP_INCREASE，等于当前血量是空操作，降低则按扣血路径处理并记入 damages；十一、所有带 now_ms 的调用必须非递减，时间回退抛 PHASE_TIME_REVERSED 且不改动血量、冷却与统计；十二、reset_encounter 清空战斗但保留阶段定义，返回 {"reset": True, "at_ms": now}，history 按时间倒序最多 max_events 条；十三、snapshot 返回 version=1 的深拷贝，覆盖 phases、encounter、stats，restore 对版本、类型、负数计数抛 PHASE_SNAPSHOT_INVALID 且原状态完全不变，恢复后冷却与阶段必须能继续使用。

硬约束：damage、set_hp、cast、reset_encounter、restore 必须整段持锁，八个线程在同一时刻施放同一个刚就绪的技能只能有一个成功，其余必须收到 PHASE_COOLDOWN，不能出现两次以上成功；阶段数量、事件条数、技能列表都有上限，禁止无界增长；快照必须与内部对象隔离，恢复后继续战斗不能污染快照。验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 困难
