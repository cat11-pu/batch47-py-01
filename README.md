# GSB 业务任务

## 题面

上周合服之后，客服组给背包问题单独拉了一个看板。玩家从活动邮件里收取补偿材料，只要背包装满就直接丢件，日志里连一条失败原因都没有；跨容器搬运脚本把背包和仓库的重量各算一遍，仓库实际已经超重却仍然接受入库；两组各 99 瓶的药水搬进仓库后显示成一整格 198 瓶，超出单格堆叠上限，客户端数量对不上，使用时报错；搬运请求超时重发，同一批物资被搬了两次，仓库里凭空多出一份；还有玩家反馈从满仓状态往背包回搬时，格子和重量两边都没校验。问题集中在 gsb/batch47-game-a-06 的 inventory_weight_transfer.py，核心类 InventoryTransferService，运营要求这一版把容器容量、物品堆叠、重量核算和搬运动作全部收口。

公开接口（Python 类型注解）：`InventoryTransferService(*, max_batch: int = 50, clock=None)`；`create_container(container_id: str, *, slots: int, weight_limit: int) -> dict`；`define_item(item_id: str, *, stack_limit: int = 1, unit_weight: int = 1) -> dict`；`grant(container_id: str, item_id: str, count: int) -> dict`；`force_grant(container_id: str, item_id: str, count: int) -> dict`；`move(container_id: str, item_id: str, *, to_container: str, count: int, request_id: str | None = None) -> dict`；`move_batch(ops, *, request_id: str | None = None) -> dict`；`usage(container_id: str) -> dict`；`is_over_encumbered(container_id: str) -> bool`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 InventoryError(code, detail)，code 用下面的字符串。

故障与边界：一、容器重复注册时 slots 与 weight_limit 完全相同按幂等返回 container_id、slots、weight_limit 三项，任一不同抛 INV_CONFLICT，slots 或 weight_limit 小于 1 抛 INV_INVALID；二、物品定义重复且参数相同幂等返回 item_id、stack_limit、unit_weight，参数不同抛 INV_ITEM_CONFLICT，未定义物品参与发放或搬动抛 INV_UNKNOWN_ITEM，容器不存在抛 INV_UNKNOWN_CONTAINER；三、usage 返回 container_id、slots、slots_used、slots_free、weight、weight_limit、weight_free、over_by、over_encumbered 九项，格子占用按向上取整（数量除以堆叠上限）逐物品累加；四、grant 必须同时满足格子和重量上限，超格子抛 INV_SLOT_FULL，超重量抛 INV_WEIGHT_EXCEEDED，失败时容器完全不变，成功返回该容器的 usage；五、force_grant 跳过两项限制用于补偿落库，落库后 over_encumbered 为 true、over_by 为实际超出重量、weight_free 取 0 不为负，同样返回 usage；六、move 的 count 必须为正整数且不超过源容器数量，否则抛 INV_INVALID 或 INV_INSUFFICIENT，源与目标相同抛 INV_INVALID；七、搬运按堆叠上限合并进目标已有堆，格子数按合并后的总量重算，不得凭空多出整堆，返回 item_id、count 以及 from、to 两个容器的 usage；八、目标放不下时抛 INV_SLOT_FULL 或 INV_WEIGHT_EXCEEDED，源和目标都不允许出现半完成状态；九、move_batch 最多 max_batch 条，超出抛 INV_BATCH_TOO_LARGE，整批要么全部生效要么全部回滚，任一 op 失败整批拒绝并计入 rejected，成功返回 count、moved_units 与 results 列表，results 按入参顺序逐条给出 item_id、count、from、to；十、带 request_id 的搬运必须幂等，重放返回首次结果且不重复搬动，同一 request_id 换参数抛 INV_REQUEST_CONFLICT；十一、snapshot 返回 version 为 1、包含 items、containers、stats 的深拷贝，containers 下每个容器含 slots、weight_limit、items，restore 遇到结构或数值非法抛 INV_SNAPSHOT_INVALID 且原状态不变；十二、stats 返回 moves、moved_units、batches、rejected 四个计数，moves 按单次搬运次数加上批量中每条 op 的次数累加，moved_units 按实际搬运件数累加，reset_stats 全部归零。

硬约束：搬运全程持锁，多线程同时搬最后一份物资只能有一个成功，其余抛 INV_INSUFFICIENT；request_id 缓存与统计更新必须和搬运在同一个临界区内完成；单次搬运与批量搬运的工作量受 max_batch 约束，不允许一次遍历全部容器。

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
