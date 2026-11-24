# 视频预览雪碧图规划

## 题面

视频后台给每个片源生成拖动预览用的雪碧图，最近两周频繁返工：同一部片子重新导出后时间轴上的缩略图对不上镜头，关键帧吸附有时候跳到十几秒以外；页数按源逐个算，十个源各自没超限，打包时却发现总页数翻了一倍，CDN 上的图集互相覆盖；编辑改过关键帧后旧图集还在被播放器引用，只能手工清缓存。这段规划逻辑在 `gsb/batch47-game-g-03` 的 `sprite_sheet_planner.py`，类 `SpriteSheetPlanner`，负责片源登记、抽帧时间规划和图集页数预算。

对外接口（Python 类型注解）：
`SpriteSheetPlanner(*, tile_width: int = 160, tile_height: int = 90, columns: int = 10, rows: int = 10, max_pages: int = 8, max_sources: int = 16, snap_ms: int = 1000)`；
`add_source(source_id: str, *, duration_ms: int, interval_ms: int, keyframes=()) -> dict`；
`remove_source(source_id: str) -> dict`；
`set_keyframes(source_id: str, keyframes) -> dict`；
`plan(source_id: str, *, start_ms: int = 0, end_ms: int | None = None) -> dict`；
`commit(source_id: str, *, start_ms: int = 0, end_ms: int | None = None) -> dict`；
`active_plan(source_id: str) -> dict | None`；
`invalidate(source_id: str) -> dict`；
`capacity() -> dict`；
`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。
校验失败抛 `SpriteError(code, detail)`，时间单位毫秒。

故障与边界：

一、构造参数里 `tile_width`、`tile_height`、`columns`、`rows`、`max_pages`、`max_sources` 必须为正，`snap_ms` 不得为负，否则抛 `SPRITE_CONFIG_INVALID`。

二、`add_source` 的 `duration_ms` 和 `interval_ms` 必须为正，否则抛 `SPRITE_BAD_SOURCE`；重复登记同一 `source_id` 且三个参数完全一致时幂等并返回 `created` 为 `False`，任一参数不同抛 `SPRITE_CONFLICT`；片源数达到 `max_sources` 抛 `SPRITE_SOURCE_LIMIT`；成功返回 `source_id`、`duration_ms`、`interval_ms`、`keyframes`、`tiles_per_page`、`created`。

三、`keyframes` 必须是 0 到 `duration_ms` 之间严格递增的整数（允许空），乱序、重复、越界或非整数一律抛 `SPRITE_BAD_KEYFRAMES`，而且登记失败不能留下半个片源。

四、抽帧时间从 `start_ms` 开始、每 `interval_ms` 取一个，包含等于 `end_ms` 的边界；`end_ms` 为空时取片源时长。`start_ms` 为负、`end_ms` 超过片源时长、`end_ms` 小于等于 `start_ms` 都抛 `SPRITE_RANGE_INVALID`。

五、有关键帧时，每个抽帧时间吸附到距离最近的关键帧；距离相同取时间较早的那个；距离超过 `snap_ms` 则保持原时间；吸附后重复的时间只保留第一张。

六、瓦片按行优先铺进页面：`page` 为序号除以每页张数取整，`row` 为页内序号除以列数取整，`col` 为页内序号对列数取余；`sheet_width` 等于列数乘 `tile_width`，`sheet_height` 等于行数乘 `tile_height`。

七、`plan` 必须返回 `source_id`、`range`、`tiles`、`tile_count`、`page_count`、`tiles_per_page`、`sheet_width`、`sheet_height`，每个瓦片带 `index`、`time_ms`、`page`、`row`、`col`，且不修改任何已提交状态。

八、单个计划的页数超过 `max_pages` 时，`plan` 与 `commit` 都抛 `SPRITE_PAGE_LIMIT`，不得缓存半成品，`active_plan` 仍为空。

九、全局页预算：提交时按“其他片源已提交计划占用的页数 + 本次计划页数”与 `max_pages` 比较，超出抛 `SPRITE_PAGE_LIMIT`；同一片源重复提交只替换自己那份占用，不能把旧页数继续累加。

十、提交成功把该片源的 `generation` 加一并保存计划副本；`active_plan` 每次返回深拷贝，调用方改返回值不能影响内部状态；未提交返回 `None`，未知片源抛 `SPRITE_UNKNOWN_SOURCE`；`commit` 返回 `generation`、`tile_count`、`page_count`，`active_plan` 返回带 `generation` 的计划副本。

十一、`set_keyframes` 校验通过后更新关键帧并让该片源已提交计划失效，返回 `generation` 与 `invalidated`；`invalidate` 再调用一次返回 `False`；`remove_source` 释放占用页数和名额，删除后查询按未知片源处理。

十二、`snapshot`/`restore` 覆盖片源参数、关键帧、`generation`、已提交计划和提交计数，快照是深拷贝；结构非法抛 `SPRITE_SNAPSHOT_INVALID`，原状态不得被破坏；快照结构为 `{"version": 1, "sources": {...}, "stats": {...}}`，片源下含 `active`；`capacity` 键为 `sources`、`max_sources`、`pages_used`、`max_pages`、`tiles_per_page`；`stats` 键为 `sources`、`commits`、`rejects`，`reset_stats` 把计数类字段归零。

硬约束：所有读写全程持锁，8 个线程同时提交同一片源时 `generation` 必须依次递增且每个线程拿到不同值，最终只保留一份计划、页预算只按一份计算；全局页数是硬上限，任何来源都不能超卖；重启恢复后已提交计划、失效状态和页占用必须与恢复前完全一致。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- difficulty: 困难
