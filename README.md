# 交叉淡化播放列表时间线

## 题面

电台改版做整点串烧，导播排了二十多条音频，交叉淡化一上就出问题：前一条的淡出比后一条的淡入长，重叠被算成两段，音量在交界处塌陷；裁剪片头片尾后时间线没有重排，后面的条目压在前一条身上；导出时按毫秒塞进采样点，接缝处有爆音；条目删掉后总时长还对不上；有人把同一条目提交两次，列表里重复出现；素材总量超过当日预算后仍然被接收。范围是 gsb/batch47-game-h-03 下的 crossfade_planner.py，类 CrossfadePlanner，负责条目增删裁剪、时间线重排、交叉淡化长度、增益曲线与采样边界。

公开接口：`CrossfadePlanner(*, sample_rate=48000, max_entries=256, max_total_ms=86400000)`；`add_entry(entry_id, *, uri, duration_ms, fade_in_ms=0, fade_out_ms=0) -> dict`；`remove_entry(entry_id) -> dict`；`trim_entry(entry_id, *, head_ms=0, tail_ms=0) -> dict`；`entry(entry_id) -> dict`；`plan() -> dict`；`gain_at(entry_id, offset_ms) -> int`；`sample_bounds(entry_id) -> dict`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 CrossfadeError(code, detail)。

故障与边界：条目 id 或 uri 为空抛 CF_ENTRY_INVALID，重复 id 抛 CF_ENTRY_EXISTS，时长不大于零抛 CF_DURATION_INVALID，淡化时长为负或超过条目时长抛 CF_FADE_INVALID；超过 max_entries 抛 CF_ENTRY_LIMIT；全部条目有效时长之和超过 max_total_ms 抛 CF_BUDGET_EXCEEDED，且本次添加或裁剪不得留下半截状态；plan 按添加顺序首尾相接，相邻重叠取前一条 fade_out 与后一条 fade_in 的较小值，后一条起点等于前一条起点加时长再减重叠，crossfades 给出 left、right、起止毫秒、重叠毫秒与重叠采样数，无重叠则不入列；gain_at 只接受 0 到条目有效时长之间的偏移，越界抛 CF_OFFSET_INVALID，未知条目抛 CF_ENTRY_UNKNOWN，淡入增益按千分比向下取整，淡出从尾部反向计算，两端同时生效时取较小值；trim_entry 累计 head 与 tail，有效时长等于源时长减两者，结果不大于零抛 CF_TRIM_INVALID，淡化值超过新时长时截到新时长；remove_entry 删除后相邻条目立即重排，未知条目抛 CF_ENTRY_UNKNOWN；sample_bounds 按 sample_rate 把起点、终点、淡入淡出换算成整数采样并返回四个字段；snapshot 顶层为 version/entries/stats，深拷贝，非法结构抛 CF_SNAPSHOT_INVALID 且原状态不变；stats 含 added、removed、trimmed、plans 四项。

空列表时 plan 返回 total_ms 为 0，entries 与 crossfades 都是空数组；计划里的每条 entry 带 entry_id、start_ms、end_ms、duration_ms、fade_in_ms、fade_out_ms 六个字段；重叠采样数按 sample_rate 向下取整；entry 与 plan 都返回副本，调用方改写不得影响内部状态。

硬约束：添加、裁剪、删除与读取计划共用同一把锁，八个线程同时添加同一个条目只能成功一次，其余全部得到 CF_ENTRY_EXISTS；plan 与 entry 返回副本，调用方改写返回值不得影响内部状态；条目数与总时长都是硬上限，任何失败请求都必须保持原时间线不变；快照要能跨进程恢复，恢复后继续添加或裁剪得到的重叠与起点必须与未重启时一致。

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
