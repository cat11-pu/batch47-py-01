# GSB Bugfix Task

## 题面

一版丛林场景更新后，光追阴影帧率从 40 掉到 8：美术每帧只拖动三个道具，整棵顶层加速结构却被全量重建；另一个场景恰好相反，几百个实例的变换都改了，调度器只挑最前面几个 refit，剩下的脏实例整帧没有下文，下一帧又从头来一遍；还有道具换模型之后只做了 refit，阴影直接错位；集群滚动重启后待处理实例全部丢失，重启第一帧画面出现大面积缺影。

范围是 gsb/batch47-game-d-02 下的 bvh_refit_scheduler.py，类 BvhRefitScheduler，负责实例登记、脏标记、每帧 refit 与 rebuild 决策和预算分配。公开接口：`BvhRefitScheduler(*, rebuild_ratio: float = 0.5, frame_budget: int = 100, max_instances: int = 512, refit_cost: int = 1, rebuild_cost: int = 4)`；`add_instance(instance_id: str, *, mesh_id: str, transform, coverage: float = 0.0) -> dict`；`update_instance(instance_id: str, *, transform, coverage: float | None = None) -> dict`；`set_mesh(instance_id: str, *, mesh_id: str) -> dict`；`remove_instance(instance_id: str) -> None`；`is_dirty(instance_id: str) -> bool`；`instance_state(instance_id: str) -> dict`；`pending() -> dict`；`compact() -> int`；`begin_frame(frame: int, *, budget: int | None = None) -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`；`reset() -> None`。拒绝统一抛 RefitError(code, detail)。

故障与边界：一、变换必须是 12 个有限数，覆盖度必须在 0 到 1 之间，网格名不能为空，否则分别报非法变换、非法覆盖度和非法网格；二、重复添加同一实例拒绝，存活实例数达到 max_instances 也拒绝；三、内容完全相同的 update 返回 changed 为假、版本号不变、也不产生新的脏标记；四、同一个网格重复 set_mesh 是空操作，换成别的网格必须置拓扑脏并把版本加一；五、pending 里的脏实例按覆盖度从大到小、实例 ID 升序排列，拓扑脏单独列出；六、脏实例占比达到 rebuild_ratio，或者存在拓扑脏时，预算够就整树重建并清掉所有脏标记；七、占比没到阈值就只 refit 脏实例，重建代价超出预算时降级为按优先级 refit；八、预算连一个 refit 都放不下时本轮什么都不派发，全部记入 deferred，下一帧继续；九、拓扑脏的实例不能被单独 refit，只能等整树重建；十、帧号必须严格递增，重复或回退的帧号直接拒绝，每帧预算可以选择性覆盖；十一、remove 只留墓碑，墓碑占着实例 ID，直到 compact 才回收，回收后可以重新添加；十二、快照要带上实例、墓碑、帧号和统计，恢复后帧号只能继续往前走，重启后的第一帧不能重放已经处理过的实例，非法快照整体拒绝且不动现有状态；同一份实例状态和同一帧号在任意进程里算出的派发顺序必须逐项相同。

硬约束：所有入口持同一把可重入锁，多线程同时对同一实例写入相同变换只允许产生一次版本变化；快照是深拷贝，恢复后原对象继续更新不影响恢复方；每帧派发量和 deferral 必须严格受预算约束，不允许一帧无限展开。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- difficulty: 地狱
