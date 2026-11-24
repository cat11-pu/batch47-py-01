# GSB 任务：GPU 帧间暂存上传环的对齐分配、回绕与退休

## 题面

美术组在开放世界场景里把骨骼矩阵、实例变换和界面顶点统一写进一块常驻映射的暂存环，最近两周快速旅行回主城后开始出现模型错位、贴图花屏，重启进程才恢复。上传环由三个渲染线程共享，帧号由主线程统一推进，任何一次分配失败都不能污染偏移游标和计数器。相关实现集中在 gsb/batch47-game-e-01/gpu_upload_ring.py，核心类型是 UploadRing，配套异常 UploadError，失败时通过 code 返回错误码。现在要把它做成可长期驻留、跨帧安全复用的上传环。

对外接口保持如下形态：UploadRing(*, capacity=1048576, alignment=256, frames_in_flight=2)；begin_frame(frame_id) 返回 {frame, auto_retired}；allocate(size, *, alignment=None, tag="") 返回 {handle, offset, size, alignment, frame, tag}；write(handle, data, *, offset=0) 返回写入字节数；read(handle, *, offset=0, length=None) 返回 bytes；release(handle) 立即归还单块；retire_frame(frame_id) 返回该帧释放的块数；usage() 返回容量、已用、空闲、对齐空洞、块数和活跃帧；snapshot() 与 restore(state) 负责跨进程恢复；stats() 与 reset_stats() 暴露计数器。调用方会按字段逐项读取，键名不能改也不能漏。

需要落到的具体行为：一，分配偏移按对齐向上取整，默认 256，显式对齐必须是二的幂且不超过 4096，否则报 RING_INVALID_ALIGNMENT；size 非正报 RING_INVALID_SIZE。二，capacity 必须为正且能被默认对齐整除，否则 RING_INVALID_CAPACITY；frames_in_flight 非正报 RING_INVALID_FRAMES。三，写入越过块尾或 read 区间越界都报 RING_WRITE_OVERFLOW，且原有字节不得被改动。四，尾部剩余空间不足时必须回绕到头部空档，回绕次数计入 wraps；仍放不下报 RING_OVER_CAPACITY，失败分配不得消耗 handle。同一帧内先分配的块偏移必须小于后分配的块，退休后的空闲字节要立刻可被后续帧使用。五，handle 从 1 单调递增，失败不跳号；释放或退休后旧 handle 报 RING_STALE_HANDLE，从未发放的 handle 报 RING_BAD_HANDLE。六，begin_frame 的帧号必须严格递增，否则 RING_FRAME_ORDER；活跃帧达到 frames_in_flight 时自动退休最老一帧，并在 auto_retired 中报告。七，retire_frame 只能按开始顺序退休，重复退休返回 0，从未开始的帧报 RING_UNKNOWN_FRAME。八，退休只释放该帧的块，其他帧的数据和偏移不得变化，全部帧退休后 head 与 padding 归零。九，begin_frame 必须在没有当前帧时也能直接开启第一帧，分配归属当前帧。十，snapshot 要保存块内容、handle 游标、活跃帧、对齐空洞和帧号，restore 到同配置新实例后内容、句柄和后续分配位置一致；结构损坏、版本不符或容量不符报 RING_SNAPSHOT_INVALID，且不得留下半套状态。十一，usage 中 used 与 free 之和必须恒等于 capacity，padding 单独统计。padding 只累计对齐和回绕跳过的字节，已释放块不能重复计入。十二，stats 至少统计 allocations、released、retired、bytes_written 和 wraps，reset_stats 后全部归零。

硬约束：八个线程同时分配时不能出现区间重叠或重复 handle；快照恢复后旧句柄语义保持一致；容量上限是硬边界，不能靠扩容掩盖。验收以 python3 -m pytest -q tests 为准，15 条必须全部通过；不得修改或跳过测试，不得按固定输入写死结果；并发与恢复场景自行构造验证；只做本地实现，不接上游提交。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

| 字段 | 值 |
| --- | --- |
| name | batch47-game-e-01 |
| domain | 游戏、图形与多媒体 |
| sub | 图形渲染 |
| type | Bugfix |
| difficulty | 地狱 |
| module | gpu_upload_ring |
| typeName | UploadRing |
| file | gpu_upload_ring.py |
