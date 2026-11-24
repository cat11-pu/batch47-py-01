# GSB 任务

## 题面

第三人称动作关卡进入冲刺段后，画面拖影成了玩家投诉最多的问题：镜头快速横移时角色轮廓被拉出半屏长的灰带，过场切镜后上一镜的残影还叠在新镜头里；角色受击位移、复活和传送时速度瞬间跨过半张屏幕，模糊核把整帧抹平，HUD、伤害数字和准星一起被拖花；性能组抓帧还发现爆炸中心的碎屑反而看不清，原因是相邻慢速物体所在屏幕块把高速块的最大半径稀释，单帧采样数也随全场对象线性上涨。范围是 gsb/batch47-game-f-01 的 motion_blur_guard.py，类 MotionBlur，承担逐帧屏幕速度收集、切镜识别、分块半径统计、卷积核生成与预算规划。公开接口：MotionBlur(*, width: int, height: int, tile_size: int = 16, max_blur_px: float = 32.0, sample_count: int = 8, cut_threshold: float = 96.0, teleport_scale: float = 4.0, budget_units: int = 200000)；begin_frame(*, frame_id: int, camera_delta=(0.0, 0.0), reset: bool = False) -> dict；submit_object(object_id: str, *, screen_delta, depth: float, screen_rect, priority: int = 0) -> dict；clamp_vector(vector) -> tuple；cut_detected(camera_delta) -> bool；set_strength(strength: float) -> None；classify_tiles() -> dict；build_kernel(tile_x: int, tile_y: int) -> list；encode(*, budget_units: int | None = None) -> dict；stats() -> dict；reset_stats() -> None；snapshot() -> dict；restore(state: dict) -> None。所有非法输入抛 MotionBlurError(code, detail)，错误码按下面列出的字符串。一要收掉重复开帧：同一 frame_id 再次 begin_frame 返回 duplicate，不清空对象也不累计帧数，帧号回退抛 MB_FRAME_REGRESSION；二要把切镜与普通帧分开：切镜阈值只按相机位移的欧氏长度判断，与方向无关，超过 cut_threshold 或 reset 为真时清空本帧速度并记 cuts，切镜帧不得产出模糊；三要拦住非法提交：未开帧抛 MB_NO_FRAME，空对象编号抛 MB_INVALID_OBJECT，屏幕矩形越界或宽高非正抛 MB_INVALID_RECT，深度非正抛 MB_INVALID_DEPTH，速度不是二元组抛 MB_INVALID_VECTOR；四要限制强度范围：只接受零到一，超出抛 MB_STRENGTH_RANGE，速度先乘强度再按 max_blur_px 截断；五要识别瞬移：位移长度超过 max_blur_px 乘 teleport_scale 时速度归零、半径记零、计入 teleports，但对象仍要登记；六要保证同帧重复提交幂等：同一对象以最后一次为准，只算一个对象，对象优先级只做登记，不参与块内半径比较；七要按块取最大半径：只统计半径大于零的块，块内取覆盖该块对象的最大像素半径，并列时按先行后列的扫描顺序取块；八要生成正确核：采样点沿块速度从负半个向量铺到正半个向量，权重和为一，非活动块或瞬移对象返回单点恒等核；九要算清预算：工作量为活动块数乘采样数加对象数乘十，超预算抛 MB_BUDGET_EXCEEDED，且不落计划、不加计数；十要能恢复：快照覆盖帧号、强度、切镜标记、对象速度与矩形、统计，恢复后重复提交仍按重复处理，结构非法抛 MB_SNAPSHOT_INVALID 且原状态不变；统计分开记录帧、对象、瞬移、切镜、核与计划次数，重置只清计数。硬约束：begin_frame 和 submit_object 全程持锁，八个线程同时开同一帧只能有一个新帧，其余必须拿到重复结果；快照必须是深拷贝，恢复后的写入不得污染已取出的状态；统计与块表不得随帧无界增长。验收：python3 -m pytest -q tests 的 15 条测试全过，不得改测试、跳过或按固定输入写死，并发与恢复自行验证，只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- difficulty: 地狱
