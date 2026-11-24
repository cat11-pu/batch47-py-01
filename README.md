# GSB 任务：命令缓冲池的租借录制状态机与在飞帧回收

## 题面

多线程渲染器把阴影、后处理和拷贝录进同一组命令缓冲，压测时出现某帧命令被下一帧覆盖、同一缓冲重复提交，GPU 直接丢设备。相关代码在 gsb/batch47-game-e-04/command_buffer_pool.py，核心类型是 CommandPool，异常 CommandPoolError 通过 code 给出原因。需要把租借、录制、提交、按帧回收做成严格的状态机，并让在飞帧数量受控。池大小按主线程配置固定，三套线程各自录制，又有共享拷贝任务穿插，任何状态迁移都必须原子。

接口保持：CommandPool(*, size=16, frames_in_flight=2, max_recorded_commands=1024)；begin_frame(frame_id) 返回 {frame, open_frames}；acquire(*, label="") 返回 {id, frame, label, state, commands}；record(command_id, command) 返回当前条数；submit(command_id) 返回 {id, frame, commands}；reclaim(frame_id) 返回回收的缓冲数；state(command_id)；usage()；leak_check(current_frame=None)；snapshot() 与 restore(state)；stats() 与 reset_stats()。返回字典的键名按约定提供，调用方逐项读取。

具体行为：一，size、frames_in_flight、max_recorded_commands 非正分别报 POOL_INVALID_SIZE、POOL_INVALID_FRAMES、POOL_INVALID_LIMIT。二，没有当前帧就 acquire 报 POOL_NO_FRAME；所有缓冲都被占用报 POOL_EXHAUSTED；非法 id 报 POOL_BAD_HANDLE，state 对未知 id 要报错而不是返回空闲；label 不是字符串报 POOL_INVALID_LABEL。三，缓冲 id 固定为 1 到 size，释放后优先复用编号最小的空闲缓冲，acquire 返回的状态必须是 recording 且命令数为 0。空闲缓冲按编号升序取用，回收后空闲表重新排序。四，record 只接受 recording 状态和字典命令，否则报 POOL_BAD_STATE 或 POOL_INVALID_COMMAND；单缓冲达到上限报 POOL_RECORD_LIMIT，已有命令不得被截断。五，submit 只允许 recording，重复提交或提交空闲缓冲报 POOL_BAD_STATE。六，reclaim 只能按开始顺序回收最老的开启帧，乱序报 POOL_FRAME_ORDER，从未开始的帧报 POOL_UNKNOWN_FRAME，同一帧重复回收返回 0。回收后该帧从开启列表移除，必须重新 begin_frame 才能再次归属。七，只要该帧还有 recording 缓冲，reclaim 必须整体拒绝并报 POOL_BUFFER_BUSY，detail 给出缓冲 id 列表，不能出现一半已回收一半仍提交的状态。八，begin_frame 的帧号必须严格递增，否则 POOL_FRAME_ORDER；未回收帧数量达到 frames_in_flight 时再开新帧报 POOL_FRAME_BUDGET，失败不得推进帧号。九，回收要把命令、标签和帧号清空并回到 idle，之后只能重新 acquire 才能录制，旧命令对象要一并丢弃，防止下一次录制读到脏数据。十，leak_check 以当前帧或显式帧号减去 frames_in_flight 为门槛，返回仍挂在旧帧上的缓冲 id 排序列表，leak_check 本身不能改变任何状态。十一，snapshot 要保存开启帧、已回收帧、空闲表、每缓冲状态和命令内容，restore 到同配置实例后状态一致，且能继续提交与回收；版本、配置、空闲表或槽位不一致报 POOL_SNAPSHOT_INVALID，失败不改动现有池。命令内容按深拷贝保存，恢复后提交计数保持原值。十二，stats 至少统计 acquired、submitted、reclaimed、recorded、frames，usage 中 allocated 加 free 等于 size，recording 加 submitted 不超过 allocated。

硬约束：帧预算和回收顺序是硬边界；八个线程并发 acquire 不能拿到同一个缓冲；快照恢复后未完成的工作不能丢。回收必须整帧成功或整帧失败。验收以 python3 -m pytest -q tests 为准，15 条必须全部通过；不得修改或跳过测试，不得按固定输入写死结果；并发、预算耗尽和恢复场景自行验证；只做本地实现，不接上游提交。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

| 字段 | 值 |
| --- | --- |
| name | batch47-game-e-04 |
| domain | 游戏、图形与多媒体 |
| sub | 图形渲染 |
| type | Bugfix |
| difficulty | 困难 |
| module | command_buffer_pool |
| typeName | CommandPool |
| file | command_buffer_pool.py |
