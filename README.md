# GSB 任务：Bindless 资源句柄的代际校验与延迟回收

## 题面

材质热更上线后，部分 draw call 仍握着上一版贴图的绑定句柄，采样到刚被回收重用的显存，画面出现整块红色。绑定表实现在 gsb/batch47-game-e-03/bindless_handle_table.py，核心类型是 HandleTable，异常 HandleError 通过 code 返回错误码。需要让句柄带代际校验，并让回收严格落后于 GPU 在飞帧。绑定表同时服务材质、网格和采样器，热更期间读写非常频繁。

对外接口：HandleTable(*, capacity=64, frames_in_flight=2)；insert(resource, *, kind="texture", tag="") 返回打包句柄；resolve(handle) 返回 {handle, index, generation, kind, tag, resource}；contains(handle) 返回布尔；generation_of(handle) 返回当前代际；retire(handle) 标记待回收；free_now(handle) 立即回收；begin_frame(frame_id) 返回 {frame, reclaimed}；usage() 返回容量、存活、待回收、空闲和当前帧；snapshot() 与 restore(state)；stats() 与 reset_stats()。所有字典返回值都按约定键名提供，不能省略字段。

具体行为要求：一，句柄按 index 占低 24 位、generation 占高位打包，generation 从 1 开始，0 或超出容量索引一律报 HANDLE_INVALID。句柄必须是正整数，索引越界和代际不符要区分处理。二，capacity 或 frames_in_flight 非正报 HANDLE_INVALID_CAPACITY 或 HANDLE_INVALID_FRAMES。三，kind 只接受 texture、buffer、sampler，非法报 HANDLE_INVALID_KIND；tag 必须是字符串，否则 HANDLE_INVALID_TAG；resource 必须是字典，否则 HANDLE_INVALID_RESOURCE，且失败插入不得占用槽位。四，槽位用完报 HANDLE_TABLE_FULL；已经 retire 但尚未回收的槽位仍占容量，不能提前复用。insert 失败后，下一次成功插入仍要拿到最小可用索引。五，retire 后旧句柄立刻报 HANDLE_STALE，但槽位要到 frame_id 减去 frames_in_flight 大于等于退休帧时才回收；begin_frame 帧号必须严格递增，否则 HANDLE_FRAME_ORDER。六，回收后再次插入同一槽位必须把 generation 加一，旧句柄永远失效；free_now 走同一套代际递增逻辑但立即归还。free_now 也必须把槽位放回空闲表，保证下一次插入能立刻复用。七，空闲槽位按后进先出复用，最近退休的最先被重新分配，顺序要可预测。空闲顺序在快照恢复后也要保持。八，resolve 返回的 resource 必须是拷贝，调用方改写不能影响表内数据；contains 对非法或过期句柄返回 False 而不抛错。九，usage 中 live 加 pending 加 free 必须等于 capacity，待回收槽位不能计入空闲。十，snapshot 要保存槽位代际、存活与待回收帧、空闲顺序和资源内容，restore 到同配置实例后存活句柄可解析、过期句柄仍过期、后续复用的槽位与代际一致。待回收槽位要保持原代际，直到真正回收时才加一。十一，快照版本不符、容量不符、空闲表重复、代际为 0 或槽位结构损坏都报 HANDLE_SNAPSHOT_INVALID，失败时不得改动现有表，不得先清空再校验。十二，stats 至少统计 inserted、retired、reclaimed、reused、stale_hits 和 freed_now，reset_stats 清零。

硬约束：八个线程并发插入不能拿到重复句柄或同一槽位；回收必须遵守在飞帧延迟，不能提前释放；容量上限不可突破。同一帧重复 begin_frame 必须拒绝，帧号只能前进。验收以 python3 -m pytest -q tests 为准，15 条必须全部通过；不得修改或跳过测试，不得按固定输入写死结果；并发和恢复场景自行验证；只做本地实现，不接上游提交。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

| 字段 | 值 |
| --- | --- |
| name | batch47-game-e-03 |
| domain | 游戏、图形与多媒体 |
| sub | 图形渲染 |
| type | Bugfix |
| difficulty | 困难 |
| module | bindless_handle_table |
| typeName | HandleTable |
| file | bindless_handle_table.py |
