# GSB 任务说明

## 题面

仓库改版后客服收到一串背包投诉：一次发放 7 件再发 6 件，第二批发进了已满的堆叠还把总数算错；仓库存取提示仓库已满，但背包明明有空位；拆分堆叠后原格数量少了一件；绑定材料和不绑定材料混进同一堆后无法交易；跨服转移时背包扣了、仓库没到；服务器重启后背包与仓库合计对不上；活动批量发放并发写入，最后少了 3 件；背包只剩一个空格时，往仓库搬 99 件只成功了一部分。范围是 gsb/batch47-game-a-02 下的 inventory_stack_manager.py，类 InventoryStackManager，负责背包与仓库的槽位、堆叠上限、绑定区分、拆分合并和状态快照。

公开接口（Python 类型注解）：`InventoryStackManager(*, bag_slots=20, warehouse_slots=40, stack_size=99)`；`grant(player: str, item_id: str, count: int, *, bound: bool = False) -> dict`；`remove(player: str, item_id: str, count: int, *, bound=None) -> dict`；`move_to_warehouse(player: str, item_id: str, count: int, *, bound=None) -> dict`；`move_to_bag(player: str, item_id: str, count: int, *, bound=None) -> dict`；`split_stack(player: str, slot_index: int, count: int) -> dict`；`merge_stacks(player: str, from_slot: int, to_slot: int) -> dict`；`discard(player: str, slot_index: int, count: int) -> dict`；`bag(player: str) -> list`；`warehouse(player: str) -> list`；`capacity(player: str) -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 InventoryError(code, detail)。

故障与边界：一、grant 先填充同物品同绑定且未满的堆叠（槽位升序），再占用最小空闲槽；绑定与不绑定永远分开堆；单次新增不得让任何堆叠超过 stack_size；二、可用容量不足时整次 grant 失败抛 INV_NO_SPACE，背包不得有任何变化；三、remove 未指定 bound 时先扣不绑定堆叠再扣绑定堆叠，数量不足抛 INV_INSUFFICIENT 且不部分扣除，扣空的槽位立即释放；四、split_stack 要求 1 小于等于 count 且 count 小于原堆数量，否则抛 INV_INVALID_SPLIT；没有空闲槽抛 INV_NO_SPACE；新堆继承物品与绑定标记；五、merge_stacks 只允许同物品同绑定的两个不同槽位，合计超过 stack_size 抛 INV_STACK_OVERFLOW，物品或绑定不同抛 INV_INCOMPATIBLE，源槽必须清空；六、move_to_warehouse 先校验背包数量再校验仓库容量，任一不足即整体失败（INV_INSUFFICIENT 或 INV_WAREHOUSE_FULL），不允许背包已扣仓库未加；未指定 bound 时取最低槽位那一堆的绑定标记；七、move_to_bag 反向同理，背包满抛 INV_NO_SPACE 且仓库数量不变；八、discard 只作用于背包槽位，数量超过该槽存量抛 INV_INSUFFICIENT，扣到零释放槽位；九、capacity 返回背包与仓库的已用槽位和总槽位，grant、移动、丢弃后立即反映；十、snapshot 深拷贝所有玩家的背包、仓库与统计，restore 后继续 grant 和搬运与快照时一致，结构非法（槽位越界、重复槽位、数量超过 stack_size、绑定标记非布尔）抛 INV_SNAPSHOT_INVALID 且原状态不变；十一、stats 中 granted、removed、moved、discarded 记单位数，merged、split 记操作次数，rejected 记被拒次数；十二、多线程同时 grant 不得超出槽位上限或丢单位，多线程搬运前后背包加仓库的总数必须守恒。

硬约束：grant、remove、拆分、合并与双向搬运都在同一把锁内完成校验和写入，失败时两侧都不许留半成品；快照必须能跨进程恢复并保持槽位、绑定与堆叠结构；槽位与堆叠上限是硬预算，任何操作都不允许通过临时溢出绕过。

验收：`python3 -m pytest -q tests` 的 15 条必须全部转绿；不得改动测试、跳过用例或针对固定输入写死结果；并发与重启恢复要自行覆盖；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 地狱
