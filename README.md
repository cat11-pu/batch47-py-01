# GSB Bugfix Task

## 题面

开启光追软阴影后，降噪器每帧把整屏 tile 全部重算，GPU 时间被吃满，可真正需要处理的只有镜头切换后历史失效的那一片；反过来，高方差 tile 和低方差 tile 混在一起派发，低方差区域反复收敛却占着预算，画面里颗粒感迟迟不消；镜头一切，旧历史没有整体作废，鬼影拖了两三帧；窗口从 4K 拉成 1080p，旧网格的 tile 还在缓存里，派发下标直接越界。

范围是 gsb/batch47-game-d-05 下的 denoise_tile_scheduler.py，类 DenoiseTileScheduler，负责 tile 网格、方差与样本上报、历史失效、每帧派发计划与合并。公开接口：`DenoiseTileScheduler(*, tile_size: int = 16, budget: int = 8, min_samples: int = 4, variance_threshold: float = 0.25, history_frames: int = 2)`；`configure_grid(width: int, height: int) -> dict`；`mark_tile(tile_id: str, *, variance: float, samples: int, history_valid: bool = True) -> dict`；`add_region(x0: int, y0: int, x1: int, y1: int, *, variance: float, samples: int) -> list`；`invalidate_history(reason: str = "camera_cut") -> int`；`plan() -> dict`；`tile_state(tile_id: str) -> dict`；`pending() -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`；`reset() -> None`。拒绝统一抛 TileError(code, detail)。

故障与边界：一、网格按 tile_size 向上取整生成 r{行}c{列} 形式的 tile，宽高必须为正整数，尺寸不变时重复配置是幂等，尺寸变化要清空旧 tile 状态；二、没有配置网格时不允许上报、派发、按区域标记或查询 tile；三、方差必须是非负有限数，样本数必须是非负整数，历史标记必须是布尔，越界 tile 一律报未知；四、内容完全相同的重复上报返回 changed 为假、代数不变，内容变化才把代数加一并置脏；五、历史失效、样本数低于 min_samples、方差达到 variance_threshold 三者满足其一就算待处理；六、派发顺序按方差从大到小、样本数从小到大、tile 编号升序，每帧最多 budget 个；七、按派发顺序把同一行且列号连续递增的 tile 合并成一个 dispatch 分组，跳列或换行都要另起一组；八、被派发的 tile 样本数加一、历史置为有效、脏标记清掉、代数加一，没被派发的保持脏；九、样本数收敛且方差低于阈值的 tile 不再进入待处理，高方差 tile 允许连续多帧派发；十、invalidate_history 只统计真正从有效翻成失效的 tile，第二次调用返回零，且下一次 plan 要带出 history_reset 和原因，再下一次清掉；十一、add_region 按闭区间按行优先标记矩形，坐标越界、起点大于终点都拒绝，且必须整体生效；十二、快照覆盖网格、tile、帧号、待处理的重置标记与原因、统计，恢复后帧号继续往前走，非法快照整体拒绝且不动现有状态。

硬约束：预算、样本阈值和方差阈值都要在配置时校验，非法组合不能等到第一帧才暴露；上报、失效、区域标记和派发共用同一把锁，八个线程同时上报同一个 tile 的相同内容只允许发生一次代数变化；每次 plan 的派发量与 deferred 必须严格受 budget 约束，不允许同一帧重复展开；tile 顺序、分组和统计不得依赖字典或哈希顺序。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- difficulty: 困难
