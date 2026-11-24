# TS 流时间戳与连续性修复

## 题面

直播频道上周批量接入第三方编码器，TS 流一进转码集群就出故障：跨零点前后的流被整段判成过期时间戳，播放器直接黑屏；编码器重启后时间戳从零重来，被当成几小时的跳变丢弃；连续性计数器断掉两三个包没人记账，字幕和音轨开始错位；网关重发的同一个包被算成两帧；运维手工复位后第一个包仍被判计数器错位；某路流混进非法 PID，把整条流水线拖垮。范围是 gsb/batch47-game-h-01 下的 transport_clock_guard.py，类 TransportClockGuard，负责单路 TS 流的注册、包校验、33 位时间戳展开、计数器修复与快照恢复。

公开接口：`TransportClockGuard(*, wrap_bits=33, tick_hz=90000, max_jump_ms=5000, max_streams=64, batch_limit=256, cc_loss_budget=8, pids_per_stream=32)`；`open_stream(stream_id, *, program=1) -> dict`；`close_stream(stream_id) -> None`；`push_packet(stream_id, packet) -> dict`；`push_batch(stream_id, packets) -> list`；`stream_state(stream_id) -> dict`；`unwrap(stream_id, raw, *, kind="pts") -> int`；`repair(stream_id) -> dict`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。open_stream 返回 stream_id、program 与已登记 PID 列表，初始为空数组；repair 返回 pids_reset 计数。校验失败统一抛 TsClockError(code, detail)。

故障与边界：同一 stream_id 同 program 重复注册幂等，program 不同抛 TS_STREAM_CONFLICT，超过 max_streams 抛 TS_STREAM_LIMIT；PID 限 0–8190、CC 限 0–15、pts/dts 限 2 的 wrap_bits 次方内，越界分别抛 TS_PID_INVALID、TS_CC_INVALID、TS_TIMESTAMP_INVALID，字段缺失或类型不对抛 TS_PACKET_INVALID；DTS 大于 PTS 抛 TS_DTS_AFTER_PTS；33 位时间戳按模回绕展开，跨回绕后返回连续绝对 tick，unwrap 沿用该路最近基准；相邻差值超过 max_jump_ms 且未标 discontinuity 抛 TS_CLOCK_JUMP，标记后以新基准接受并返回 discontinuity；CC 与上一包相同返回 duplicate 且不推进状态，跳号返回 gap 并给出 lost，丢包超过 cc_loss_budget 抛 TS_CC_LOSS_EXCEEDED 且不落库；repair 清空该路期望计数，下一包以 resync 接受；单路 PID 超过 pids_per_stream 抛 TS_PID_LIMIT；push_batch 超 batch_limit 整批抛 TS_BATCH_TOO_LARGE，未超时逐条处理、单条被拒不影响后续；stream_state 按 PID 给出 cc、展开后的 pts/dts 与已提交包数；snapshot 顶层为 version/streams/stats，restore 覆盖流、PID 状态、展开基准与统计，深拷贝，非法结构抛 TS_SNAPSHOT_INVALID 且原状态不变；stats 给出 packets、accepted、duplicates、gaps、discontinuities、resyncs、rejected、lost_packets 八项计数。

每路流的 pts 与 dts 分别维护展开基准，互不覆盖；每个 PID 的首包接受任意合法 CC，从第二包才开始比对期望值；repair 只清计数不清时间基准，之后仍按新计数判断重发；duplicate 不计入 accepted，也不推进 cc 与展开后的时间戳；批量中被拒的条目计入 rejected，后续条目继续处理；packets 统计所有进入校验的调用，被拒调用也算一次；close_stream 之后同 id 可以重新 open，内部状态从零开始，PID 表清空并重新等待首包；close 未登记的 stream_id 抛 TS_STREAM_UNKNOWN；snapshot 的 streams 以 stream_id 为键，pids 以整数 PID 为键。

硬约束：push_packet 与 push_batch 共用一把锁，八个线程同时推同一个包只能有一个 ok，其余全是 duplicate；重发包幂等，不重复计数也不推进时间基准；流表、PID 表与批量都有硬上限；快照跨进程可用，恢复后继续推送仍按原回绕基准展开；统计与流状态分离，reset_stats 不得清空流表、PID 表或展开基准，已取出的快照对象也不得随后续写入改变。

验收：`python3 -m pytest -q tests` 的 15 条测试必须全部通过；不得改测试、跳过或按固定输入写死；并发与恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- difficulty: 地狱
