# 任务说明

## 题面

公会战结算一上线，伤害统计就被玩家贴图质疑：同一发火球在战斗日志里打了 450，结算面板只记 225，客服查了半天才发现是抗性扣了两次、护盾吸收又没有计入减免；暴击伤害有时按 1.5 倍算、有时直接翻倍，跨职业对不上；目标只剩 50 点血时吃了一发 200 的普攻，溢出 150 点被算进击杀者的伤害榜，坦克的承伤却只记 50；吸血职业血量回满后仍继续累加治疗量，反伤把已经阵亡的攻击者又扣成负数；网络重试把同一发 hit 结算两遍，护盾被重复消耗；伤害榜按每分钟重算一次，同分玩家的名次每次刷新都跳；战斗服重启后本场战斗的承伤、击杀和最近命中全部丢失，申诉只能凭录像估算。还有一条线上告警：长局战斗的事件列表无限增长，单场内存涨到 2GB 后进程被 OOM 杀掉。

范围是 gsb/batch47-game-c-04 下的 damage_attribution_ledger.py，类 DamageAttributionLedger，负责战斗单位、命中拆解、治疗、聚合统计与快照。公开接口：`__init__(*, max_combatants: int = 64, max_events: int = 1000, hit_cache: int = 256)`；`register_combatant(combatant_id: str, *, hp: int, shield: int = 0, resistances=None, reflect_percent: int = 0, lifesteal_percent: int = 0) -> dict`；`unregister_combatant(combatant_id: str) -> bool`；`set_resistance(combatant_id: str, *, element: str, percent: int) -> dict`；`grant_shield(combatant_id: str, amount: int) -> dict`；`resolve_hit(attacker_id: str, defender_id: str, *, raw: int, element: str = "physical", crit: bool = False, hit_id=None) -> dict`；`heal(combatant_id: str, amount: int) -> dict`；`combatant_report(combatant_id: str) -> dict`；`damage_by_attacker(attacker_id: str) -> dict`；`damage_by_element() -> dict`；`leaderboard(limit: int = 5) -> list`；`recent_hits(limit: int = 10) -> list`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 DamageError(code, detail)。

故障与边界：一、战斗单位 ID 重复注册抛 DAMAGE_DUPLICATE_COMBATANT，hp 小于等于 0、护盾为负、百分比越界、抗性不在 0 到 90、元素名为空都抛 DAMAGE_BAD_COMBATANT，单位数超过 max_combatants 抛 DAMAGE_COMBATANT_LIMIT；二、命中伤害先按暴击乘 150 再整除 100，再按目标该元素抗性百分比整除 100 得到减免，护盾吸收减免后的剩余部分，最后才扣血；三、实际扣血不得超过目标当前血量，剩余部分计入 overkill；四、吸血按实际扣血的百分比整除 100 计算，先治疗攻击者，治疗量不超过其最大血量，超出部分记 overheal；五、反伤按实际扣血乘目标反伤百分比整除 100，在吸血之后从攻击者血量扣除，不足部分记 reflect_overkill；六、raw 小于等于 0、非整数或元素名为空抛 DAMAGE_BAD_AMOUNT，攻守任一方不存在抛 DAMAGE_UNKNOWN_COMBATANT；七、传入 hit_id 时首次结算并缓存，重复同一 hit_id 且参数一致直接返回 cached 结果并标 duplicate=True，不重复扣血、不重复计数，参数不一致抛 DAMAGE_HIT_CONFLICT，缓存超过 hit_cache 时淘汰最早条目并计入 hit_cache_evicted；八、heal 只对存活单位生效，阵亡单位抛 DAMAGE_TARGET_DEAD，返回 healed、overheal 与当前血量；九、damage_by_attacker 固定返回 hp_damage、overkill、resisted、shield_absorbed、hits、kills、lifesteal、reflect_taken，damage_by_element 按元素返回 hp_damage 与 hits；十、leaderboard 按 hp_damage 降序、同分按 attacker_id 升序，limit 小于 1 抛 DAMAGE_BAD_LIMIT；十一、事件列表最多 max_events 条，超出丢最旧并计入 events_evicted，recent_hits 返回最新在前；十二、snapshot 返回 version=1 的深拷贝，覆盖 combatants、dealt、taken、elements、events、hit_cache 与 stats，restore 对版本、类型、负数计数问题抛 DAMAGE_SNAPSHOT_INVALID 且原数据完全不变，恢复后 hit_id 重放仍按重复处理。

硬约束：resolve_hit、heal、grant_shield、restore 必须整段持锁，八个线程同时对同一目标打不同 hit_id 时最终扣血必须等于八次伤害之和，不能少算也不能把同一次算两遍；事件数、单位数、命中缓存都受硬上限约束，禁止无界增长；快照必须与内部对象隔离，恢复后继续结算不能污染快照。验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 困难
