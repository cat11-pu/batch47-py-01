# 审片时间码与标记区间

## 题面

审片系统上线后，剪辑师发现时间码对不上：29.97 的素材按整 30 帧换算，片尾累计差了三秒多；跨整分钟的位置标记被拒，说时间码非法；同一个镜头的标记重复导入两次，列表里出现两条；两个小组提交的标记区间交叠，导出的报告把同一段台词算了两次；把片段裁到素材边界时程序直接报错退出；服务重启后已导入的标记整批消失。范围是 gsb/batch47-game-h-02 下的 timecode_range_guard.py，类 TimecodeRangeGuard，负责时间码解析与格式化、帧与毫秒换算、片段登记、标记区间校验合并和快照恢复。

公开接口：`TimecodeRangeGuard(*, max_clips=32, marker_limit=500, max_duration_frames=10000000)`；`parse_timecode(text, *, fps, drop_frame) -> dict`；`format_timecode(frames, *, fps, drop_frame) -> str`；`frame_to_ms(frames, *, fps_num, fps_den) -> int`；`ms_to_frame(ms, *, fps_num, fps_den) -> int`；`open_clip(clip_id, *, fps_num, fps_den, start_timecode, drop_frame=False, duration_frames) -> dict`；`close_clip(clip_id) -> None`；`add_marker(clip_id, *, start, end, label) -> dict`；`markers(clip_id) -> list`；`merge_markers(clip_id, *, gap_frames=0) -> dict`；`trim_range(clip_id, *, start, end) -> dict`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 TcError(code, detail)。

故障与边界：NDF 时间码按 HH:MM:SS:FF 解析，帧号必须小于标称帧率，否则抛 TC_RATE_MISMATCH；位数或分隔符不对抛 TC_FORMAT_INVALID；NDF 用冒号、DF 用分号，混用抛 TC_FORMAT_INVALID；DF 只支持标称 30 与 60，其他帧率抛 TC_RATE_UNSUPPORTED；DF 每分钟开头跳过两个帧号，十分整分钟不跳，落在被跳过的帧号上抛 TC_INVALID_DROP；帧与毫秒换算按真实帧率向下取整，帧率为零或负抛 TC_RATE_UNSUPPORTED，负帧抛 TC_FRAME_INVALID；同一 clip_id 配置完全相同重复登记幂等，任一字段不同抛 TC_CLIP_EXISTS，超过 max_clips 抛 TC_CLIP_LIMIT，时长必须大于零且不超过 max_duration_frames，否则抛 TC_DURATION_INVALID；标记必须落在片段边界内，越界抛 TC_RANGE_OUTSIDE，结束不大于开始抛 TC_RANGE_INVERTED，标签为空抛 TC_LABEL_INVALID，单片段标记超过 marker_limit 抛 TC_MARKER_LIMIT；完全相同的标记重复添加幂等，markers 以相对片段起点的 label/start/end 帧号返回并按开始、结束、标签排序；merge_markers 只合并同标签且间隔不超过 gap_frames 的相邻区间，负间隔抛 TC_MERGE_GAP_INVALID，返回 before/after/markers 并替换存储；trim_range 把请求区间裁到片段边界并返回相对起止与长度，完全落在片段外抛 TC_RANGE_OUTSIDE；snapshot 顶层为 version/clips/stats，clips 下每个片段带 markers 列表，深拷贝，非法结构抛 TC_SNAPSHOT_INVALID 且原状态不变；stats 含 parsed、formatted、conversions、markers_added、markers_merged、trims 六项。

标记值以相对片段起点的帧号存储，绝对时间码只用于入参；合并成功后 markers_merged 加一，裁剪成功一次 trims 加一；format_timecode 对负帧抛 TC_FRAME_INVALID；片段的标称帧率由 fps_num 与 fps_den 四舍五入得到，drop_frame 只允许标称 30 或 60。

硬约束：标记的查重与写入必须在同一把锁内完成，八个线程同时导入同一条标记只能落一条，统计只加一次；合并、裁剪与删除后的列表必须保持稳定顺序，重放同一请求不得产生重复条目；标记数量、片段数量与时长都有硬上限；快照可跨进程恢复，恢复后继续登记与合并仍保持原顺序和统计。

验收：`python3 -m pytest -q tests` 的 15 条测试必须全部通过；不得改测试、跳过或按固定输入写死；并发与恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- difficulty: 困难
