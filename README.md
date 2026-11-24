# 混音参数自动化包络

## 题面

混音工程从老工作站迁移自动化数据后，音量曲线在播放器里和原工程对不上：同一秒内导入了两个断点，结果曲线来回跳；批量粘贴一段自动化后，越界值把整条轨道写成静音；按拍导出的断点换算成毫秒时四舍五入方向不一致，副歌入口整体偏了十几毫秒；保存工程再打开，步进曲线被当成线性曲线，包络全变。处理这段逻辑的是 `gsb/batch47-game-g-05` 的 `automation_lane.py`，类 `AutomationLane`，另有模块级换算函数 `beats_to_ms` 和 `ms_to_beats`。

对外接口（Python 类型注解）：
`AutomationLane(*, curve: str = "linear", max_points: int = 1000, min_value: float = -60.0, max_value: float = 12.0)`；
`create_lane(lane_id: str, *, default_value: float = 1.0, curve: str | None = None) -> dict`；
`drop_lane(lane_id: str) -> dict`；`apply(lane_id: str, points, *, mode: str = "merge") -> dict`；
`remove_point(lane_id: str, time_ms: int) -> dict`；`value_at(lane_id: str, time_ms: int) -> float`；
`sample(lane_id: str, start_ms: int, end_ms: int, step_ms: int) -> list`；
`clear_range(lane_id: str, start_ms: int, end_ms: int) -> dict`；
`lane_info(lane_id: str) -> dict`；`list_lanes() -> list`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。
校验失败抛 `MixError(code, detail)`。

故障与边界：

一、构造参数 `curve` 只接受 `linear` 和 `step`，`max_points` 必须为正，`min_value` 必须小于 `max_value`，否则抛 `MIX_CONFIG_INVALID`。

二、`create_lane` 的 `lane_id` 不能为空，为空抛 `MIX_BAD_PARAM`；`default_value` 必须有限且落在取值范围内；同一轨道重复创建且默认值和曲线一致时幂等并返回 `created` 为 `False`，任一不同抛 `MIX_CONFLICT`；未创建轨道上的任何读写抛 `MIX_UNKNOWN_LANE`；成功创建返回 `lane_id`、`curve`、`default_value`、`created`。

三、`apply` 的点必须是“时间, 数值”二元组：时间必须是非负整数，数值必须是有限数且落在 `[min_value, max_value]`，二元组结构不对抛 `MIX_BAD_POINT`，时间非法抛 `MIX_BAD_TIME`，数值非法抛 `MIX_BAD_VALUE`；`mode` 只接受 `merge` 和 `replace`，否则抛 `MIX_BAD_MODE`。

四、`merge` 按时间升序保存断点；同一时间重复提交只保留最后一条；同一批次内出现相同时间也以最后一条为准；返回的 `written` 是新时间点个数，`overwritten` 是批次开始前已存在、本次被覆盖的时间点个数，`point_count` 是批后断点数。

五、轨道断点数不得超过 `max_points`，任何一次 `apply` 只要会导致超限就整批拒绝并抛 `MIX_LANE_FULL`，已有断点一个都不能变。

六、取值规则：空轨道返回该轨道的 `default_value`；时间早于第一个断点返回第一个断点的值，晚于最后一个断点返回最后一个断点的值；正好落在断点上返回断点值；`linear` 曲线在相邻断点之间按时间线性插值，`step` 曲线取时间上不大于查询点的最后一个断点的值。

七、`replace` 模式先用本批断点整体替换轨道，再按同一套校验和上限检查；任何一个点非法都必须整批不落地，原有断点保持不变。

八、`sample` 从 `start_ms` 开始、每 `step_ms` 取一个值，包含等于 `end_ms` 的边界；`step_ms` 必须为正、`end_ms` 不得小于 `start_ms`、起点不得为负，否则抛 `MIX_BAD_RANGE`。

九、`clear_range` 删除时间落在闭区间内的断点并返回 `cleared` 条数和剩余 `point_count`，区间外断点保留，删除后两侧断点之间照常插值；起点大于终点抛 `MIX_BAD_RANGE`。

十、`remove_point` 删除不存在的断点不报错，返回 `removed` 与 `point_count`，不存在时为 `False`；`lane_info` 给出 `lane_id`、`curve`、`default_value`、`point_count`、`first_time`、`last_time`、`min_value`、`max_value`，空轨道的时间与极值为 `None`；`list_lanes` 按 `lane_id` 升序。

十一、`beats_to_ms` 按 `beats * 60000 / bpm` 计算并四舍五入到整数毫秒，`ms_to_beats` 按 `ms * bpm / 60000` 计算并保留 6 位小数；`bpm` 不大于 0 抛 `MIX_BAD_TEMPO`，拍数或毫秒为负抛 `MIX_BAD_TIME`。

十二、`snapshot`/`restore` 覆盖轨道曲线、默认值、全部断点和写入计数，快照结构为 `{"version": 1, "lanes": {...}, "stats": {...}}`，轨道下含 `curve`、`default_value`、`points`，且是深拷贝；结构非法抛 `MIX_SNAPSHOT_INVALID` 且原状态不变；`stats` 键为 `writes`、`overwrites`、`rejects`，`reset_stats` 全部归零。

硬约束：应用断点、删除区间、恢复快照全程持锁，多线程并发写同一轨道时断点必须始终有序、同刻断点最终只保留一个、计数与实际写入一致；单轨断点数是硬上限，任何模式都不能绕过；重启恢复后曲线类型、断点值和取值结果必须与恢复前一致。

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
