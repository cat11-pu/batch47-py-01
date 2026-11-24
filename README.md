# 任务题面

## 题面

公会科技树上个月改版后，研发面板的数字和实际扣款对不上，会长连发三次截图。有公会同时开四个研究项目，界面显示上限是二，第二天两个进度条同时归零；有人把贡献点投进已完成的节点，点数扣了但科技等级没涨；贡献按自然日重置，海外分部凌晨投入的点数被算进前一天，第二天开服就报额度已满。取消进行中的研究，有的公会返还全部成本，有的只返一半，客服拿不出统一口径。加速道具更乱：同一项目连加三次，剩余时间反而变长；预算用完之后还能继续点，界面不报错。服务器重启后，进行中的研究全部消失，已投入的贡献点却还在，玩家截图投诉。另有反馈说，前置科技其实已完成，后置节点仍显示锁定，重登后才恢复。

范围是 gsb/batch47-game-b-02 下的 guild_research_lab.py，类 GuildResearchLab，负责科技节点定义、贡献点归集、研究启动与完成、加速、取消和状态快照。公开接口：`GuildResearchLab(*, max_concurrent=2, refund_ratio=0.5, daily_cap=500, boost_budget=600)`；`define_node(node_id, *, name, tier, cost, duration, requires=()) -> dict`；`status(node_id) -> dict`；`contribute(member_id, amount, *, now=None) -> dict`；`pool() -> int`；`start(node_id, *, now=None) -> dict`；`complete_due(*, now=None) -> list`；`boost(node_id, seconds, *, now=None) -> dict`；`cancel(node_id, *, now=None) -> dict`；`completed() -> list`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 ResearchError(code, detail)。

故障与边界：一、节点定义必须带正整数等级、成本、时长，缺项或非法抛 RESEARCH_INVALID_NODE，节点 ID 自引用或形成环路抛 RESEARCH_CYCLE；二、同一节点以完全相同的名称、等级、成本、时长和前置列表重复定义时幂等返回，任一项不同抛 RESEARCH_NODE_CONFLICT；三、status 对未定义节点抛 RESEARCH_UNKNOWN_NODE，返回状态只能是 available、locked、active、completed，locked 时 missing_requires 按字母序列出未完成前置；四、贡献金额必须是正整数，零、负数、布尔或小数抛 RESEARCH_INVALID_AMOUNT，空成员 ID 抛 RESEARCH_INVALID_MEMBER，失败不得改动池子；五、每个成员按 UTC 自然日累计，当日已投加本次超过 daily_cap 抛 RESEARCH_DAILY_CAP 且一分不扣，跨日自动从零开始；六、start 依次校验：已完成抛 RESEARCH_ALREADY_COMPLETED，进行中抛 RESEARCH_ALREADY_ACTIVE，前置未完成抛 RESEARCH_PREREQ_MISSING 并带缺失列表，并发项目数达到 max_concurrent 抛 RESEARCH_CONCURRENCY_LIMIT，池子不足抛 RESEARCH_INSUFFICIENT_POINTS；七、启动成功才扣成本，记录 started_at 与 finish_at，返回 active 状态和剩余池子；八、complete_due 只结算 finish_at 不晚于 now 的项目，按完成时间、再按启动序号排序，返回行的 node_id、tier、completed_at 必须准确，重复调用不得二次结算；九、cancel 只对进行中的项目生效，返还 int(cost × refund_ratio) 向下取整，节点回到 available 且可重新启动，已完成抛 RESEARCH_ALREADY_COMPLETED，空闲抛 RESEARCH_NOT_ACTIVE；十、boost 只对进行中的项目生效，秒数必须为正整数，实际生效秒数不超过剩余时间，累计加速超过 boost_budget 抛 RESEARCH_BOOST_BUDGET 且 finish_at 不变；十一、completed 按完成先后返回节点 ID，stats 恰好包含 contributed、started、completed、cancelled、boosted、refunded 六个累计值，reset_stats 全部归零；十二、snapshot 必须覆盖节点表、完成表与完成顺序、进行中项目、池子、成员累计、每日额度、已用加速预算和统计，restore 遇到版本不符、字段类型错误、完成顺序与完成表不一致、进行中项目缺时间戳时抛 RESEARCH_SNAPSHOT_INVALID 且原状态不变。

硬约束：贡献、启动、完成、加速、取消共用一把可重入锁，十个线程同时给同一成员投 100 点、日上限 500 时，只能成功五笔且池子正好 500；snapshot 必须是深拷贝，跨进程恢复后进行中项目的 finish_at、每日额度和统计都要能继续使用，已结算项目不得复活；并发项目数、加速预算、每日额度三个上限都必须拒绝越界请求而不是截断；restore 先完整校验再整体替换，任何失败都不能留下半套状态。时间参数接受带时区的 ISO 8601 字符串，缺省取当前 UTC 时间。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 困难
