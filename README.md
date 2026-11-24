# GSB 任务

## 题面

摄影模式上线第一周，玩家把焦点锁在远处主角脸上，近景草丛仍被糊成整块色板；自动对焦从近景切到远景时，中间十几次快门每一张的虚化程度都不一样；半分辨率散景在高光点周围留下方形空洞，补洞逻辑把周围采样直接取平均，亮斑变成灰斑；光圈从 2.0 调到 4.0，同一块石头的虚化半径几乎没有变化；关卡美术按 32 像素 tile 标注景深，规划时发现相邻块一个清晰一个全糊，近景层和远景层被算进同一份采样预算，单帧开销翻倍。目标文件是 gsb/batch47-game-f-02 的 depth_of_field_guard.py，类 DepthOfField，负责焦距动画、CoC 计算、tile 分层、散景核生成和预算。接口：DepthOfField(*, width: int, height: int, tile_size: int = 16, focus_distance: float = 10.0, aperture: float = 2.0, coc_scale: float = 10.0, max_coc_px: float = 24.0, focus_speed: float | None = None, sample_count: int = 8, budget_units: int = 200000)；set_focus(distance: float, *, speed: float | None = None) -> dict；update_focus(dt: float) -> float；compute_coc(depth: float) -> float；linearize_depth(raw: float, *, near: float = 0.1, far: float = 500.0) -> float；set_aperture(aperture: float) -> None；set_tile_depths(depths) -> dict；plan_tiles() -> list；build_bokeh_kernel(coc_px: float, *, samples: int | None = None) -> list；plan(*, budget_units: int | None = None) -> dict；stats() -> dict；reset_stats() -> None；snapshot() -> dict；restore(state: dict) -> None。失败抛 DofError(code, detail)。一、CoC 按光圈乘 coc_scale 再乘深度与焦距的相对偏差计算，结果夹在零到 max_coc_px 之间，焦距处必须为零，深度非正抛 DOF_INVALID_DEPTH。二、焦距支持瞬间切换和按速度推进两种模式，速度为空的 set_focus 立即到位，有速度时只改目标，焦距非正抛 DOF_INVALID_FOCUS，速度非正抛 DOF_INVALID_SPEED，update_focus 按速度乘 dt 步进且不得越过目标，dt 为负或非数字抛 DOF_INVALID_DT。三、光圈只接受正数，非法抛 DOF_INVALID_APERTURE，改完光圈后 CoC 立即按新值计算。四、tile 深度表必须是行优先的一维序列，长度等于横向块数乘纵向块数，长度不符抛 DOF_GRID_MISMATCH，任一深度非正抛 DOF_INVALID_DEPTH。五、未设置深度表就规划抛 DOF_NO_DEPTHS。六、逐块分层按当前焦距判断，小于焦距为 near，大于为 far，相等为 in_focus，并给出该块的 CoC，块号按行优先顺序与输入位置一致。七、散景核在 CoC 为零或单采样时退化为中心单点且权重为一，否则采样点均匀分布在半径一半的圆环上，权重和为一，负 CoC 抛 DOF_INVALID_COC，采样数非正抛 DOF_INVALID_SAMPLES。八、规划的工作量为块数乘十加需要散景的块数乘采样数，超预算抛 DOF_BUDGET_EXCEEDED 且不写计划、不加计数，也不得清掉上一份成功计划。九、恢复要带回焦距、目标、速度、光圈、深度表、统计和上一份计划，结构非法抛 DOF_SNAPSHOT_INVALID 且原状态不变。十、统计分开记录更新、步进、深度表、核与计划次数，重置后归零但焦距和深度表保持。硬约束：update_focus 和恢复过程都持锁，八个线程各推一步必须得到连续的焦距序列，不能丢步或重复计数；快照为深拷贝，后续改动不得回写；恢复时深度表长度必须与当前画幅匹配；统计与计划只保留当前值，不随调用堆积。验收：python3 -m pytest -q tests 的 15 条测试全部通过，不得修改测试、跳过或按固定输入写死，并发和恢复自行验证，只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- difficulty: 困难
