# 音频波形峰值金字塔

## 题面

音频编辑器打开两小时访谈，波形面板一拖动就卡死：每次缩放都重扫整段 PCM，界面十几秒无响应；长录音末尾的峰值显示不出来；导入 16 位小端 WAV 时，奇数长度文件把缓冲区写坏；素材超过内存预算后程序直接 OOM；两个人同时往同一段录音追加，总长度对不上；服务重启后波形金字塔要重新扫一遍；查询几十秒的区间返回上万个桶，前端直接崩。范围是 gsb/batch47-game-h-05 下的 waveform_peak_index.py，类 WaveformPeakIndex，负责增量追加采样、多分辨率峰值桶、区间查询、内存预算与快照恢复。

公开接口：`WaveformPeakIndex(*, sample_rate=48000, base_bucket=256, max_levels=8, max_samples=48000000, max_query_buckets=4096)`；`append(samples) -> dict`；`append_pcm16(data) -> dict`；`level_for(ms_per_pixel) -> int`；`query(start_ms, end_ms, *, ms_per_pixel) -> list`；`peaks(*, level) -> list`；`summary() -> dict`；`reset() -> None`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 WaveError(code, detail)。

故障与边界：样本必须是 -32768 到 32767 的整数，越界、浮点或非数字整批拒绝并抛 WP_SAMPLE_INVALID，失败批次不得写入任何样本；append_pcm16 只接受字节串且长度必须为偶数，奇数长度或非字节抛 WP_PCM_INVALID，按小端有符号 16 位解码；第 L 层桶大小为 base_bucket 乘 2 的 L 次方，peaks 只返回已完成桶，append 返回 appended、completed_buckets、total_samples；累计样本超过 max_samples 抛 WP_BUDGET_EXCEEDED，且本次不入账；level_for 在 ms_per_pixel 不大于零时抛 WP_RANGE_INVALID，否则取桶时长不超过 ms_per_pixel 的最粗层，没有可用层则返回 0；query 在起点小于零或终点不大于起点时抛 WP_RANGE_INVALID，终点超过总样本时截到末尾，起点已在数据之后返回空列表，每条结果给出 index、start_sample、end_sample、min、max、rms、complete，未满的尾桶 complete 为假，rms 为均方值开方后向下取整，桶数超过 max_query_buckets 抛 WP_QUERY_TOO_DENSE；peaks 对越界层号抛 WP_LEVEL_INVALID，返回该层已完成桶的 min 与 max；reset 清空样本与各层桶但保留统计；snapshot 顶层为 version/levels/stats，并带 total，restore 恢复后继续 append 必须接上未满桶，非法结构抛 WP_SNAPSHOT_INVALID 且原状态不变；stats 含 appends、pcm_appends、samples_written、buckets_closed、queries 五项。

summary 返回 samples 与各层 completed 计数，completed 按层号从低到高排列；query 的层号由 level_for 自动选择，peaks 每项只含 min 与 max，0 层是最细桶；query 返回的 end_sample 不超过当前总样本数；桶的毫秒宽度由 sample_rate 与 base_bucket 决定；未满桶在每一层单独维护，查询时只返回所选层的一个尾桶。

硬约束：append、append_pcm16、query 与 reset 共用同一把锁，多线程并发追加时总样本数必须精确等于各次长度之和，各层已完成桶数按总量整除计算，全局最小最大值不得因交错而错乱；预算检查必须在写入前完成，失败批次不能留下半桶数据；层数、单批长度与查询桶数都有硬上限，不允许无界增长；快照可跨进程恢复，恢复后继续追加得到的桶边界与未重启时一致。

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
