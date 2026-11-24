# GSB 业务任务

## 题面

装备修理改版之后，金币流水对不上账，运营先停掉了自动修理：同一把武器点两次修理，第二次耐久已经满了还照扣一笔钱；有玩家把耐久修成负数，客户端显示 -3/10，攻击力反而更高；批量修理一次修了四十多件装备，中途余额不足，前二十件修好了，后面的没修，金币却全扣了；折扣券和基础修理费叠在一起，策划算出来是 114 金，线上扣了 135 金；离线挂机回来点修理，请求重发放了两遍，背包里金币少了两份，耐久只涨了一次。问题集中在 gsb/batch47-game-a-10 的 equipment_durability_repair.py，核心类 DurabilityLedger，需要把耐久扣减、修理计价和扣费口径定死。

公开接口（Python 类型注解）：`DurabilityLedger(*, base_fee: int = 10, per_point: int = 5, max_batch: int = 32, clock=None)`；`grant_currency(player: str, amount: int) -> int`；`balance(player: str) -> int`；`register(player: str, item_id: str, *, max_durability: int, tier: int) -> dict`；`use(player: str, item_id: str, *, wear: int = 1) -> dict`；`durability(player: str, item_id: str) -> int`；`is_broken(player: str, item_id: str) -> bool`；`broken_items(player: str) -> list`；`repair_cost(player: str, item_id: str, *, points=None, discount: int = 0) -> dict`；`repair(player: str, item_id: str, *, points=None, discount: int = 0, request_id: str | None = None) -> dict`；`repair_all(player: str, *, discount: int = 0, request_id: str | None = None) -> dict`；`status(player: str) -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 DurabilityError(code, detail)，code 用下面的字符串。

故障与边界：一、register 的 max_durability 取 1 到 10000、tier 取 1 到 5，越界抛 DUR_INVALID；同一玩家同一件装备重复注册且参数相同幂等返回，参数不同抛 DUR_CONFLICT；返回 player、item_id、max_durability、tier、durability 五项。二、未注册的装备查询、使用、修理一律抛 DUR_UNKNOWN_ITEM；grant_currency 金额为负抛 DUR_INVALID。三、use 的 wear 必须为正整数，否则抛 DUR_INVALID；耐久扣到 0 为止不得为负，扣到 0 时 broken 为 true，继续使用已损坏装备抛 DUR_BROKEN。四、use 返回 player、item_id、durability、max_durability、broken；broken_items 按 item_id 升序返回。五、计价公式：单点价格为 per_point 乘以 tier，gross 等于修理点数乘以单点价格，先取 max(base_fee, gross)，再按折扣做整数下取整 cost 等于该值乘以（100 减 discount）除以 100，discount 取 0 到 50，越界抛 DUR_INVALID。六、repair_cost 的 points 缺省为当前缺失耐久，必须是 1 到缺失值之间的整数，否则抛 DUR_INVALID；未受损装备的 repair_cost 返回 points 为 0、gross 为 0、cost 为 0。七、repair_cost 返回 item_id、points、unit_price、gross、discount、cost 六项。八、repair 成功后按 cost 扣余额、按 points 恢复耐久，返回 player、item_id、points、cost、durability、balance；余额不足抛 DUR_INSUFFICIENT_FUNDS，耐久与余额都不变；对满耐久装备修理抛 DUR_NOT_DAMAGED。九、repair_all 只处理受损装备，逐件按各自的 floor 费用求和，余额不足整批回滚，受损件数超过 max_batch 抛 DUR_BATCH_TOO_LARGE，没有受损装备时返回 repaired 为 0、cost 为 0，有修理时返回 player、repaired、cost、balance。十、带 request_id 的 repair 与 repair_all 必须幂等，重放返回首次结果且不重复扣费，同一 request_id 换参数抛 DUR_REQUEST_CONFLICT。十一、status 返回 player、items、broken、durability_total、max_total；stats 返回 uses、wear、repairs、repaired_points、spent 五个计数，reset_stats 全部归零。十二、snapshot 返回 version 为 1、包含 players 与 stats 的深拷贝，players 下每个玩家含 items 与 balance，items 下每件装备含 max_durability、tier、durability，restore 遇到结构或数值非法抛 DUR_SNAPSHOT_INVALID 且原状态不变。

硬约束：扣耐久、计价、扣费与统计更新必须在一把锁内完成，多线程同时修理同一件装备只能有一个成功扣费，其余抛 DUR_NOT_DAMAGED；repair_all 的整批校验与扣费要么全部生效要么全部回滚，不允许修一半；批量修理的件数受 max_batch 限制，单次操作不允许遍历玩家全部装备。

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
