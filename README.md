# 连播音频拼接会话

## 题面

连播编排服务上线后，剪辑师在后台拼好的有声书时段每天有几十条在播出时对不上：两段素材接缝处偶尔爆音，段间静音忽长忽短，导出的总时长和后台预览能差几百毫秒；还有编辑改到一半点了发布，结果整条时段被锁死，只能重开一条。相关逻辑集中在 `gsb/batch47-game-g-01` 的 `audio_clip_assembler.py`，类 `AudioClipAssembler`，负责草稿时间线、接缝规划和提交。

对外接口（Python 类型注解）：
`AudioClipAssembler(*, sample_rate: int = 48000, max_clips: int = 64, default_fade: int = 480, max_fade: int = 2400)`；
`create_session(session_id: str, *, gap_samples: int = 0, max_duration: int | None = None) -> dict`；
`drop_session(session_id: str) -> None`；
`add_clip(session_id: str, clip_id: str, *, samples: int, fade_in: int | None = None, fade_out: int | None = None, gap_after: int | None = None) -> dict`；
`remove_clip(session_id: str, clip_id: str) -> dict`；
`move_clip(session_id: str, clip_id: str, position: int) -> dict`；
`preview(session_id: str) -> dict`；
`commit(session_id: str) -> dict`；
`reopen(session_id: str) -> dict`；
`capacity(session_id: str) -> dict`；
`status(session_id: str) -> dict`；
`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。
所有校验失败统一抛 `AudioError(code, detail)`，`code` 用下面列出的字符串。

具体的故障与边界：

一、构造参数不合法（`sample_rate` 不大于 0、`max_clips` 小于 1、`default_fade` 为负或大于 `max_fade`）抛 `AUDIO_CONFIG_INVALID`，不允许带着坏参数开工。

二、`create_session` 对同一 `session_id` 且 `gap_samples`、`max_duration` 完全一致时幂等，返回 `created` 为 `False`；任一参数不同抛 `AUDIO_CONFLICT`；`gap_samples` 为负或 `max_duration` 不为空且小于 1 抛 `AUDIO_BAD_PARAM`；成功创建返回 `session_id`、`gap_samples`、`max_duration`、`created`。

三、任何针对未创建会话的读写都抛 `AUDIO_UNKNOWN_SESSION`；`drop_session` 之后该会话再出现同样处理。

四、同一会话内 `clip_id` 重复抛 `AUDIO_DUPLICATE_CLIP`；`samples` 小于 1、`clip_id` 为空串、`gap_after` 为负都抛 `AUDIO_BAD_PARAM`；新增成功返回 `clip_id`、`index`、`samples`、`fade_in`、`fade_out`、`gap_after`、`clip_count`。

五、`fade_in`、`fade_out` 传空时取 `default_fade`；为负抛 `AUDIO_BAD_PARAM`；超过 `max_fade` 或超过该片段 `samples` 的一半抛 `AUDIO_FADE_TOO_LONG`。

六、会话片段数达到 `max_clips` 时再新增抛 `AUDIO_SESSION_FULL`，该片段不得写入，`capacity` 返回 `max_clips`、`clip_count`、`remaining`。

七、接缝规则：相邻两段前一段 `fade_out` 和后一段 `fade_in` 都大于 0 时按交叉淡化处理，`overlap_samples` 取两者较小值，该接缝的间隔一律按 0 计；否则按静音间隔处理，间隔取前一段的 `gap_after`，为空则取会话的 `gap_samples`。总时长等于各片段样本数之和减去所有交叉淡化重叠、加上所有静音间隔，`duration_seconds` 由总样本数除以 `sample_rate` 并保留 6 位小数。

八、`preview` 必须给出 `session_id`、`clip_count`、`order`、`duration_samples`、`duration_seconds`、`seams`、`committed`、`generation`；`seams` 每项包含 `from`、`to`、`kind`（`crossfade` 或 `gap`）、`overlap_samples`、`gap_samples`，即使为 0 也要出现。

九、空会话提交抛 `AUDIO_EMPTY_TIMELINE`；配置了 `max_duration` 且总样本数超出抛 `AUDIO_DURATION_LIMIT`；两种失败都不能改变会话状态，也不能把会话置为已提交。

十、提交成功后会话被冻结，再增删或移动片段抛 `AUDIO_SESSION_COMMITTED`；`reopen` 解除冻结并保留 `generation`，下一次提交把 `generation` 加一。`move_clip` 的 `position` 必须落在当前片段序号范围内，否则抛 `AUDIO_BAD_POSITION`；删除不存在的片段抛 `AUDIO_UNKNOWN_CLIP`。`move_clip` 返回 `index` 与 `order`，`remove_clip` 返回 `removed` 与 `clip_count`，`commit` 返回 `committed`、`generation`、`duration_samples`、`duration_seconds`、`clip_count`、`order`，`reopen` 与 `status` 返回 `committed`、`generation`。

十一、`snapshot`/`restore` 必须覆盖全部会话的片段顺序、淡入淡出、间隔、提交状态、`generation` 和统计；快照是深拷贝，恢复前后的后续写入互不影响；结构非法统一抛 `AUDIO_SNAPSHOT_INVALID` 且原状态完全不变；快照结构为 `{"version": 1, "sessions": {...}, "stats": {...}}`，每个会话含 `clips` 列表。

十二、`stats` 精确给出活跃会话数、片段总数、成功提交次数、`reopen` 次数和所有被拒绝调用的次数，键名为 `sessions`、`clips`、`commits`、`reopens`、`rejects`，`reset_stats` 只把后四项计数归零。

硬约束：所有读写全程持锁，多线程对同一会话添加同一 `clip_id` 只能有一个成功，其余全部收到 `AUDIO_DUPLICATE_CLIP`；提交是原子的，校验失败不允许留下半截时间线；快照要能跨进程恢复，恢复后已提交的会话不能被编辑；片段数、单会话时长和淡入淡出长度都必须有硬上限，任何路径都不许绕过。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- difficulty: 地狱
