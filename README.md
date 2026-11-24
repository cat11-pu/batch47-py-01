# GSB 任务：临时渲染目标缓存的字节预算与延迟复用

## 题面

后处理链每帧要申请全分辨率、半分辨率、深度和若干临时颜色目标，最近在 4K 场景显存峰值忽高忽低，同尺寸目标复用率很低，偶尔还会把仍在采样的目标直接改作他用。实现位于 gsb/batch47-game-e-05/render_target_pool.py，核心类型是 RenderTargetPool，异常 RenderTargetError 通过 code 返回原因。需要把临时渲染目标做成带字节预算、延迟复用和最近最少使用淘汰的缓存。缓存同时服务阴影、泛光和抗锯齿三个通道，键的稳定性直接影响命中率。

对外接口：RenderTargetPool(*, budget_bytes=16777216, frames_in_flight=2, alignment=4)；acquire(width, height, *, format="rgba8", samples=1, tag="") 返回 {key, id, width, height, format, samples, bytes, frame, tag, reused}；release(key) 标记待回收；begin_frame(frame_id) 返回 {frame, retired, resident_bytes}；purge() 返回 {dropped, bytes}；state(key)；usage() 返回预算、驻留、空闲、条目、租用和待回收数量；snapshot() 与 restore(state)；stats() 与 reset_stats()。

具体行为：一，宽高必须为正且是 alignment 的整数倍，否则报 RT_INVALID_SIZE；alignment 必须是二的幂且不超过 256，budget 与 frames_in_flight 非正分别报 RT_INVALID_BUDGET 或 RT_INVALID_FRAMES。二，format 只支持 r8、rg8、rgba8、rgba16f、r32f、depth32f，未知格式报 RT_UNKNOWN_FORMAT；samples 只接受 1、2、4、8，否则报 RT_INVALID_SAMPLES；tag 必须是字符串。三，缓存键由宽、高、格式和采样数组成，字节数按宽乘高乘每像素字节再乘采样数计算，任何一项不同都必须是不同条目。键字符串采用宽 x 高:格式:采样数的形式，日志和快照都按它核对。四，尚未 begin_frame 就申请报 RT_NO_FRAME；同键条目正被租用报 RT_TARGET_BUSY，已释放但未过在飞帧报 RT_TARGET_PENDING，两者不能混为一谈。五，release 只能作用于已租用条目，重复释放报 RT_NOT_LEASED，未知键报 RT_UNKNOWN_TARGET。六，release 之后要等帧号推进到超过退休门槛才能重新命中；begin_frame 帧号必须严格递增，否则 RT_FRAME_ORDER。七，申请新条目导致驻留字节超过预算时，按 last_used 从旧到新淘汰可用的未租用条目；没有足够可淘汰空间时整体报 RT_BUDGET_EXCEEDED，不得先淘汰再失败。八，命中已有条目必须保持原 id、复用计数加一，并更新租用帧和最近使用顺序，命中的条目不能改变尺寸和字节数。九，purge 只丢弃未租用条目，包括待回收条目，返回条目数和字节数；租用中的条目不能动，purge 不改变预算与时钟。十，usage 中 resident_bytes 加 free_bytes 恒等于 budget，entries 等于租用加待回收加可用条目数。十一，snapshot 要保存预算、帧号、时钟、条目键、尺寸、格式、采样、字节、租用与待回收状态，restore 到同配置实例后命中、待回收和淘汰顺序一致；版本、预算、键、字节数或条目结构不符报 RT_SNAPSHOT_INVALID 且当前缓存不变。恢复后下一次申请要按原时钟继续递增。十二，stats 至少统计 acquires、hits、misses、releases、evicted、retired、purged，reset_stats 清零。

硬约束：预算上限是硬边界，淘汰必须按最近最少使用顺序且失败原子；延迟复用必须遵守在飞帧门槛；八个线程并发申请不同目标时不能重复分配 id 或击穿预算；快照恢复后租约和待回收状态保持一致。验收以 python3 -m pytest -q tests 为准，15 条必须全部通过；不得修改或跳过测试，不得按固定输入写死结果；并发、预算耗尽和恢复场景自行验证；只做本地实现，不接上游提交。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

| 字段 | 值 |
| --- | --- |
| name | batch47-game-e-05 |
| domain | 游戏、图形与多媒体 |
| sub | 图形渲染 |
| type | Bugfix |
| difficulty | 困难 |
| module | render_target_pool |
| typeName | RenderTargetPool |
| file | render_target_pool.py |
