# GSB 任务

## 题面

雨夜关卡把烟尘和雨雾改成柔边公告板后，角色跑过水洼时脚边粒子整片消失，贴地烟尘被地面裁成硬边；粒子沉到墙后仍按整张四边形混合，墙缝里漏出一圈亮斑；同屏几百个半透明四边形没有按层排序，前后关系每帧变化，录屏里烟尘忽前忽后；性能组发现雨幕单个四边形铺满全屏，过绘制像素数瞬间顶到预算，后续爆炸粒子被整批丢弃；发射器每帧生成新粒子、旧粒子没有回收，三分钟后名额耗尽，新的爆炸特效完全出不来；重启关卡后已生成的粒子全部消失，上一帧的统计也归零。目标文件是 gsb/batch47-game-f-04 的 soft_particle_guard.py，类 SoftParticleBatch，负责公告板登记、深度淡出、分层排序、过绘制预算与快照恢复。接口：SoftParticleBatch(*, width: int, height: int, fade_distance: float = 1.5, max_quads: int = 4096, blend_layers: int = 4, budget_units: int = 2000000)；emit(particle_id: str, *, position, size: float, depth: float, scene_depth: float, layer: int = 0) -> dict；update(particle_id: str, *, depth: float | None = None, scene_depth: float | None = None, size: float | None = None, layer: int | None = None) -> dict；remove(particle_id: str) -> None；get_particle(particle_id: str) -> dict；compute_fade(depth: float, scene_depth: float) -> float；set_fade_distance(distance: float) -> None；sort_quads() -> list；plan(*, budget_units: int | None = None) -> dict；capacity() -> dict；stats() -> dict；reset_stats() -> None；snapshot() -> dict；restore(state: dict) -> None。失败抛 ParticleError(code, detail)。一、淡出系数按场景深度减粒子深度再除以淡出距离，结果夹在零到一之间，场景深度不大于粒子深度时必须为零。二、登记校验编号、位置、尺寸、深度和层号，层号从零开始且必须小于混合层数，空编号抛 PT_INVALID_PARTICLE，位置不是二元组抛 PT_INVALID_POSITION，尺寸非正抛 PT_INVALID_SIZE，深度为负或非数字抛 PT_INVALID_DEPTH，层号越界抛 PT_INVALID_LAYER。三、同一编号重复登记抛 PT_DUPLICATE，数量达到上限抛 PT_CAPACITY，被拒绝的粒子不得占用名额。四、覆盖像素数按尺寸平方四舍五入为整数，过绘制预算按可见粒子的像素总和计算。五、更新只改传入字段并重新计算淡出，未知编号抛 PT_UNKNOWN_PARTICLE。六、删除不存在的粒子抛 PT_UNKNOWN_PARTICLE，删除后名额立即可用。七、排序只保留淡出大于零的粒子，先按层号升序，再按深度降序，最后按编号升序。八、规划结果为可见四边形数、被剔除数、像素总数、层数与工作量，工作量为像素总数加可见四边形数乘五，超预算抛 PT_OVERDRAW_BUDGET 且不落计划、不加计数。九、修改淡出距离要重算全部粒子的淡出值，非正抛 PT_INVALID_CONFIG。十、快照覆盖粒子表、淡出距离、统计和上一份计划，结构非法抛 PT_SNAPSHOT_INVALID 且原状态不变。容量查询要返回上限、已用和剩余，统计分开记录登记、更新、删除与计划次数，重置只清计数不动粒子表。硬约束：登记、更新、删除与规划全程持锁，八个线程抢最后一个名额只能有一个成功，其余必须收到 PT_CAPACITY；快照为深拷贝，恢复后继续登记不得改动已取出的状态；粒子表与统计不得无界增长。验收：python3 -m pytest -q tests 的 15 条测试全部通过，不得修改测试、跳过或按固定输入写死，并发和恢复自行验证，只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- difficulty: 困难
