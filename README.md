# GSB 任务

## 题面

竞技场关卡把实时阴影换成模板阴影体后，柱子背面的影子会突然翻到正面，角色走进光锥时整面墙闪成黑块；双向遮挡体的轮廓边只朝一个方向挤出，从背面看影子直接消失；两盏灯照同一个箱子时模板计数溢出，墙面出现镂空条纹；关掉一盏灯后旧卷仍留在缓存里，再开灯时阴影延迟半秒才更新；灯组改完聚光灯参数后，同一盏灯和同一遮挡体每帧重新挤出一遍，显存和命令缓冲都被吃满；压测关卡把同一遮挡体重复提交，命令缓冲里的卷数量翻了三倍，帧时间直接超预算。范围是 gsb/batch47-game-f-03 的 stencil_shadow_volume_guard.py，类 ShadowVolumeStencil，负责灯光登记、遮挡体几何、轮廓边挤出、模板操作与整帧规划。接口：ShadowVolumeStencil(*, stencil_bits: int = 8, max_lights: int = 4, edge_budget: int = 50000)；add_light(light_id: str, *, position, radius: float, priority: int = 0) -> dict；remove_light(light_id: str) -> None；add_occluder(occluder_id: str, *, vertices, silhouette, two_sided: bool = True) -> dict；remove_occluder(occluder_id: str) -> None；build_volume(occluder_id: str, light_id: str) -> dict；select_lights() -> list；stencil_ops(occluder_id: str, light_id: str) -> dict；set_zfail(enabled: bool) -> None；plan_frame(*, budget_edges: int | None = None) -> dict；stats() -> dict；reset_stats() -> None；snapshot() -> dict；restore(state: dict) -> None。失败抛 ShadowVolumeError(code, detail)。一、同一盏灯重复登记且参数一致返回 duplicate，参数不同抛 SV_CONFLICT；空编号、位置不是三元组、半径非正、优先级不是整数都抛 SV_INVALID_LIGHT。二、删除不存在的灯或遮挡体分别抛 SV_UNKNOWN_LIGHT、SV_UNKNOWN_OCCLUDER，删除后必须让相关卷缓存失效。三、遮挡体至少三个顶点、至少三条轮廓边，索引越界、自环、重复无向边分别抛 SV_INVALID_OCCLUDER 或 SV_DUPLICATE_EDGE，编号重复一律抛 SV_CONFLICT，不做幂等覆盖。四、构建卷要求灯和遮挡体都存在，未知对象抛对应错误；每个顶点沿远离灯的方向挤出 radius 长度，坐标保留六位小数，光源落在顶点上抛 SV_LIGHT_INSIDE。五、双向遮挡体的侧四边形数取轮廓边数两倍，单面取一倍。六、同一灯与遮挡体的卷要缓存，重复构建返回 cached，统计 built 与 cached 分开计数；切换 z-fail 后缓存必须整表失效。七、灯的选择按优先级降序、半径降序、编号升序，数量不超过 max_lights。八、模板操作前后计数都等于侧四边形数，掩码由 stencil_bits 决定，计数超过掩码抛 SV_STENCIL_OVERFLOW，z-fail 标记随当前设置。九、整帧规划跳过光源落在遮挡体内部的组合并计入 skipped，工作量为侧四边形总数加卷数乘十，超预算抛 SV_BUDGET_EXCEEDED，失败时不得留下计划或计数；规划本身只统计，不填充卷缓存。十、快照覆盖灯光、遮挡体、z-fail 与统计，非法结构抛 SV_SNAPSHOT_INVALID 且原状态不变，恢复后卷缓存从空开始、统计延续快照值。硬约束：add、build、remove 与规划都持锁，八个线程同时构建同一卷只能真正生成一次，其余命中缓存；缓存键必须同时含遮挡体与灯，任一被删除都不得留下脏卷；统计与卷缓存不得无界增长。验收：python3 -m pytest -q tests 的 15 条测试全过，不得改测试、跳过或写死输入，并发和恢复自行验证，只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- difficulty: 地狱
