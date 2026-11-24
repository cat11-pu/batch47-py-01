# GSB Bugfix Task

## 题面

上线后发现同一片森林的 draw call 数是策划预期的三倍：上百个实例本来共用同一套管线和材质，提交记录却逐条下发；把相邻记录合并之后又出现实例数超过上限、整个批次被驱动丢弃；半透明植被不管深度顺序，先画近处再画远处，画面出现明显的错误遮挡；索引预算一旦超限，整帧提交直接抛错，连之前已经排好的批次也一起丢了。

范围是 gsb/batch47-game-d-03 下的 indirect_draw_compactor.py，类 IndirectDrawCompactor，负责管线登记、提交记录收集、批次合并和间接绘制参数规划。公开接口：`IndirectDrawCompactor(*, max_instances_per_batch: int = 8, max_batches: int = 16, max_indices: int = 1000000, max_pending: int = 256)`；`register_pipeline(name: str, *, index_count: int, instanced: bool = True) -> dict`；`submit(draw_id: str, *, pipeline: str, material: str, instance_count: int = 1, transparent: bool = False, depth: float = 0.0) -> dict`；`flush() -> dict`；`pending() -> list`；`cancel(draw_id: str) -> bool`；`clear() -> int`；`describe_batch(index: int) -> dict`；`stats() -> dict`；`reset_stats() -> None`；`reset() -> None`；`snapshot() -> dict`；`restore(state: dict) -> None`。拒绝统一抛 DrawError(code, detail)。

故障与边界：一、同一管线名用不同 index_count 或不同 instanced 重复登记要报冲突，完全相同则幂等返回；二、未登记管线的提交、空材质名、非法透明标记都要拒绝；三、实例数必须在 1 到 max_instances_per_batch 之间，超出范围或为零直接拒绝；四、同一 draw_id 在 flush 之前重复提交要拒绝，待提交数达到 max_pending 也要拒绝；五、不透明条目按提交顺序合并，只有管线与材质都相同且相邻的连续条目才能进同一批次，累计实例数不能超过单批上限，超过就另开批次；六、不透明批次全部排在前面，半透明条目按深度从大到小、draw_id 升序单独成批，永远不合并；七、所有批次的总索引数超过 max_indices，或批次数超过 max_batches，flush 必须整体失败，待提交记录和统计都不能被改动；八、flush 成功后才清空待提交记录，没有待提交时再 flush 返回全零结果但 flush 次数照常累加；九、cancel 只对还在等待的条目返回真，已经 flush 出去的 ID 再取消返回假；十、describe_batch 按下标返回上一轮批次的深拷贝，越界或非法下标报未知批次；十一、统计要分开记录提交数、入批条目数、批次数、取消数和 flush 次数；十二、快照覆盖管线、待提交、上一轮批次和统计，非法快照整体拒绝且保持原状态。

硬约束：submit、cancel 与 flush 共用同一把锁，八个线程同时提交同一个 draw_id 只能有一个成功，其余全部报重复提交；flush 的预算检查必须在任何状态变更之前完成，失败后重试要得到同一份计划；快照与 describe_batch 返回的结构都是深拷贝，外部改动不能污染内部状态；同一批提交记录重复 flush 不得重复计数，失败后重试必须得到同一份批次划分。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- difficulty: 困难
