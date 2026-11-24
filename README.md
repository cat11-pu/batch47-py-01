# GSB Bugfix Task

## 题面

引擎组这周连续收到三份花屏录屏：同一帧里阴影图刚作为渲染目标写完，后处理就直接按只读纹理采样，中间没有任何状态切换记录，驱动在不同机器上给出的结果不一致；美术把一个调试通道的 pass 删掉之后，整条链路仍然在跑，还多出来十几个无效 pass；三张全屏中间纹理的生命周期完全不重叠，显存却按三份独立预算申请，4K 下直接超预算被驱动拒绝；更麻烦的是帧图连续编译两次，两次得到的 pass 顺序和屏障数量居然不同，回放工具没法做逐帧对比。

范围是 gsb/batch47-game-d-01 下的 render_graph_barrier_planner.py，类 RenderGraphBarrierPlanner，负责帧图资源登记、pass 依赖排序、死 pass 裁剪、屏障发射和显存别名规划。公开接口：`RenderGraphBarrierPlanner(*, max_barriers_per_pass: int = 8, memory_budget: int = 4096)`；`register_resource(name: str, *, kind: str = "texture", size: int = 1, imported: bool = False) -> dict`；`add_pass(pass_id: str, *, reads=(), writes=(), side_effect: bool = False) -> dict`；`mark_output(name: str) -> None`；`remove_pass(pass_id: str) -> None`；`compile() -> dict`；`barriers_for(pass_id: str) -> list`；`lifetimes() -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`；`reset() -> None`。所有拒绝统一抛 RenderGraphError(code, detail)。

故障与边界：一、资源状态只认 undefined、render_target、depth_write、shader_read、copy_src、copy_dst、present 七种，其它状态在声明时就要拒绝；二、同一个 pass 内同一资源不允许既读又写，整张图里同一个资源也只有一个写者，重复登记资源同样拒绝；三、某个 pass 读一个既没有写者、又没有标记为外部导入的资源时，要报缺失写者；四、pass 之间按写者到读者的依赖做稳定拓扑排序，出现环要报环，已经裁掉的 pass 不参与判环；五、没有被任何存活 pass 读取、也没有被标记为输出的 pass 要连同它的上游一起裁掉，标了副作用的 pass 永远保留；六、每个 pass 的屏障按先读后写、各自按声明顺序发射，前后状态相同就不发冗余屏障；七、单个 pass 的屏障数超过 max_barriers_per_pass 直接失败；八、非导入资源按首末使用区间做槽位别名，闭区间有重叠必须分槽，峰值显存超过 memory_budget 失败，资源大小按字节计，导入资源不参与别名但仍要跟踪状态；九、compile 幂等，图没有改动时重复调用返回等值结果并且只计一次实际编译，任何登记、删除、标输出都要让缓存失效；十、barriers_for 返回深拷贝，未知或已裁掉的 pass 一律报未知；十一、快照覆盖资源、pass、输出和统计，恢复后编译结果必须与快照时一致；十二、非法快照整体拒绝，且不能破坏当前已有的图。

硬约束：compile 全程持锁，多线程同时编译同一张未改动的图必须得到完全一致的结果；快照是深拷贝，能跨进程恢复，恢复之后对原对象的任何修改都不影响恢复方；排序、别名与屏障不允许依赖哈希顺序，同一输入必须永远产出同一份计划。

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
