# 语音播报排队与抢占

## 题面

坐席侧的车载语音助手上了新版本后，导航提示、来电播报和系统提醒挤在一起，用户投诉“话说到一半被掐断，后面那条又没播”，还有同一句“前方限速”在弱网重试时连播三遍；晚高峰队列堆到二十多条，内存和播报时长一起失控，值班同学只能整机重启，重启后正在播的那条又从头上开始念。处理这段逻辑的是 `gsb/batch47-game-g-02` 的 `speech_prompt_scheduler.py`，类 `SpeechPromptScheduler`，管每个通道的播报队列、抢占和过期。

对外接口（Python 类型注解）：
`SpeechPromptScheduler(*, max_pending: int = 8, default_duration_ms: int = 1200, barge_in_priority: int = 7, max_backlog_ms: int = 60000, clock=None)`；
`submit(channel_id: str, prompt_id: str, *, priority: int, duration_ms: int | None = None, ttl_ms: int | None = None, dedupe_key: str | None = None, now: int | None = None) -> dict`；
`cancel(channel_id: str, prompt_id: str) -> dict`；
`current(channel_id: str, *, now: int | None = None) -> dict | None`；
`pending(channel_id: str) -> list`；
`advance(channel_id: str, *, now: int | None = None) -> list`；
`queue_depth(channel_id: str) -> dict`；
`flush(channel_id: str) -> dict`；
`stats() -> dict`；`reset_stats() -> None`；`snapshot() -> dict`；`restore(state: dict) -> None`。
时间统一用整数毫秒，校验失败抛 `SpeechError(code, detail)`。

故障与边界：

一、构造时 `max_pending` 小于 1、`default_duration_ms` 小于 1、`barge_in_priority` 不在 0 到 9、`max_backlog_ms` 小于 1，抛 `SPEECH_CONFIG_INVALID`。

二、`priority` 不是 0 到 9 的整数抛 `SPEECH_BAD_PRIORITY`；`duration_ms` 显式传入且小于 1 抛 `SPEECH_BAD_DURATION`；`ttl_ms` 为负抛 `SPEECH_BAD_TTL`；`now` 为负抛 `SPEECH_BAD_TIME`。

三、通道空闲时提交直接开播，返回 `status` 为 `playing`、`channel`、`started_at`、`preempted`（没有抢占时为 `None`）；`current` 给 `prompt_id`、`priority`、`duration_ms`、`started_at`、`elapsed_ms`、`remaining_ms`、`deadline`（无 TTL 为 `None`），空闲返回 `None`。

四、有提示在播时，新提示按优先级降序、同优先级按提交先后排队，返回 `status` 为 `queued` 和插入后的 `position`（从 0 起）。

五、待播条数达到 `max_pending` 再提交抛 `SPEECH_QUEUE_FULL`，队列不得有任何变化。

六、积压预算按“当前提示剩余时长 + 所有待播时长 + 新提示时长”计算，超过 `max_backlog_ms` 抛 `SPEECH_BACKLOG_EXCEEDED`，同样不落地。

七、`dedupe_key` 在当前播放中或待播队列里已存在时直接合并，返回 `status` 为 `merged` 和已有 `prompt_id`，不新建条目不占预算；已播完或已过期的键可以再次入队。

八、抢占规则：新提示优先级不低于 `barge_in_priority` 且严格高于当前播放优先级时，当前提示立即终止、计入 `preempted`、不回到队列，新提示从 `now` 开始播放；其余情况只能排队，禁止打断。

九、`advance` 在 `now` 达到当前提示的 `started_at` 加 `duration_ms` 时产出 `completed` 事件并启动队首，再产出 `started` 事件；未到点返回空列表；启动前先清理所有 `deadline` 小于等于 `now` 的待播，产出 `expired` 事件，过期条目绝不能开始播放；事件统一带 `event` 和 `prompt_id`，`started` 事件另带 `started_at`。

十、`ttl_ms` 为 0 的提示提交后必然在下次推进时过期，不得占用播放位；`cancel` 只接受待播提示，返回 `status` 为 `cancelled`，取消当前提示抛 `SPEECH_NOT_CANCELLABLE`，不存在抛 `SPEECH_UNKNOWN_PROMPT`。

十一、`queue_depth` 返回 `pending`、`max_pending`、`playing`；`flush` 清空该通道全部待播并返回 `dropped` 条数，正在播的不受影响。

十二、`snapshot`/`restore` 要覆盖各通道的当前提示（含 `started_at`、`deadline`）、待播顺序、序号和全部计数，恢复后按原起点续算剩余时长；快照结构为 `{"version": 1, "channels": {...}, "stats": {...}}`，通道下含 `current`、`pending`、`seq`，且必须是深拷贝；结构非法抛 `SPEECH_SNAPSHOT_INVALID` 且原状态不变；`stats` 精确区分 `accepted`、`merged`、`preempted`、`expired`、`completed`、`cancelled`、`rejected`，`reset_stats` 全部归零；同一 `dedupe_key` 的并发提交只允许一条真正入队。

硬约束：提交、取消、推进、恢复全部持锁，多线程同一键提交只能产生一次 `accepted`，其余全部 `merged`；积压时长和待播条数都是硬上限，任何路径都不得绕过；重启恢复后当前提示的剩余时间、待播顺序和抢占能力必须与重启前一致。

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
