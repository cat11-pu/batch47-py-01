# GSB 任务

## 题面

竞技场界面换上后处理抗锯齿后，远处栏杆边缘每隔一帧闪一次，细长天线在镜头转动时断成几截；暗部噪点被误判成边缘，整片阴影泛起白边；地面网格线和高光边缘共用同一档混合系数，网格线被抹平而高光仍带锯齿；关卡按屏幕分块统计边缘数时把平坦块也算进工作量，单帧后处理预算提前用完，后半屏直接跳过；工具组用同一张五乘五亮度样本离线复算，结果和运行时对不上，原因是阈值在运行中被临时改过却没有记录。范围是 gsb/batch47-game-f-05 的 fxaa_edge_guard.py，类 FxaaEdge，负责亮度换算、边缘方向判定、混合系数、边缘统计、整帧解析与预算。接口：FxaaEdge(*, width: int, height: int, threshold: float = 0.1, relative_threshold: float = 0.15, subpixel_quality: float = 0.75, max_span: int = 8, budget_units: int = 120000)；luma(rgb) -> float；edge_contrast(grid, x: int, y: int) -> float；edge_class(grid, x: int, y: int) -> str；blend_factor(grid, x: int, y: int) -> float；detect_edges(grid) -> dict；resolve(grid) -> list；plan(grid, *, budget_units: int | None = None) -> dict；set_threshold(threshold: float, *, relative: float | None = None) -> None；stats() -> dict；reset_stats() -> None；snapshot() -> dict；restore(state: dict) -> None。失败抛 FxaaError(code, detail)。一、亮度按零点二九九红、零点五八七绿、零点一一四蓝加权，通道数不对、非数字或超出零到一抛 FX_INVALID_COLOR。二、输入网格必须正好是构造时的行数与列数，缺行、多列、非数字、越界值都抛 FX_INVALID_GRID，坐标越界抛 FX_INVALID_COORD。三、对比度取上下左右四个邻居的最大值减最小值，边界邻居按边缘复制。四、对比度低于阈值判为平坦；否则上下差绝对值不小于左右差时判为横向边缘，反之为纵向边缘。五、混合系数在平坦处为零，否则按对比度减阈值再除以相对阈值，夹到零到一后乘子像素质量。六、边缘统计返回平坦、横向、纵向、总数和最大混合系数，最大混合系数不超过子像素质量。七、解析只改非平坦像素，按上下左右平均值与当前亮度的混合系数插值，平坦像素保持原值，输入网格不得被修改，结果保留六位小数。八、规划的工作量为像素总数加边缘数乘三，超预算抛 FX_BUDGET_EXCEEDED 且不写计划、不加计数。九、阈值只接受零到一之间的开区间，相对阈值只接受零到一之间的半开区间，非法抛 FX_INVALID_THRESHOLD，修改后检测立即按新阈值执行。十、快照覆盖阈值、相对阈值、子像素质量、最大跨度、统计和上一份计划，结构非法抛 FX_SNAPSHOT_INVALID 且原状态不变。硬约束：检测与解析只读输入网格，统计计数必须持锁，八个线程并发检测同一张网格结果完全一致且计数不丢；解析不得复用调用方的行对象，必须返回全新网格；统计与计划只保留当前值，不随调用堆积。验收：python3 -m pytest -q tests 的 15 条测试全部通过，不得修改测试、跳过或按固定输入写死，并发和恢复自行验证，只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- difficulty: 困难
