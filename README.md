# 任务题面

## 题面

排位匹配池这两个赛季反复出事故，运营和客服已经把工单合并成一个长期故障单。晚高峰有支三人小队被同时塞进两个队列条目，匹配成功后在房间里站着六个相同账号；有玩家连点匹配，队伍列表出现七八份同名队伍，取消一次只消失一份；两名玩家共用一个成员位时两边都能排进去，结算时战绩记到同一个人头上。等待时间越久反而越配不上：刚定级的新号评分差六十分，等两个小时仍然原地不动；凌晨却把评分差八百的两支队伍配到一起，运营怀疑窗口根本没有上限。延迟二百毫秒的跨区队伍和三十毫秒的本区队伍被塞进同一局，进图就掉线。还有玩家匹配成功后仍留在列表里继续被配对，同一份队伍一晚上进了三局。

范围是 gsb/batch47-game-b-01 下的 matchmaking_pool_guard.py，类 MatchmakingPool，负责队列登记、票据入池、配对、取消、过期清理与状态快照。公开接口：`MatchmakingPool(*, initial_rating_window=100, widen_per_second=20, max_rating_window=600, max_latency_gap=80, ticket_ttl=120, max_pool=1000)`；`register_queue(queue_id, *, team_size, max_latency) -> dict`；`enqueue(queue_id, *, party_id, members, rating, latency, now=None, ticket_id=None) -> dict`；`cancel(queue_id, party_id, *, now=None) -> dict`；`get_ticket(queue_id, party_id) -> dict | None`；`pending(queue_id, *, now=None) -> list`；`drain(queue_id, *, now=None, limit=1) -> list`；`purge(*, now=None) -> int`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 MatchmakingError(code, detail)。

故障与边界：一、queue_id 相同且队伍人数、延迟上限一致时重复登记幂等返回，任一项不同抛 MM_QUEUE_CONFLICT，人数小于 1 或延迟上限为负抛 MM_INVALID_QUEUE；二、同一 party_id 以完全相同的成员、评分、延迟重复入池返回原票据且不重复计数，成员名单变化抛 MM_DUPLICATE_PARTY；三、成员数量必须等于队列人数，成员为空、重复或非字符串抛 MM_INVALID_PARTY，延迟超过队列上限抛 MM_LATENCY_TOO_HIGH；四、任一成员已在任一队列的票据中抛 MM_MEMBER_BUSY，且不写入；五、全池票据总数达到 max_pool 时先清理过期票据，仍满抛 MM_POOL_FULL，被拒票据不占用名额，取消后名额立刻释放；六、drain 按入池顺序贪心配对，总是取最早的可配票据与它之后最早满足条件的票据，单次最多形成 limit 组，limit 非正整数抛 MM_INVALID_LIMIT；七、评分窗口按等待秒数从 initial_rating_window 线性放宽、以 max_rating_window 封顶，两队必须同时落在双方窗口的较小值内，延迟差不超过 max_latency_gap；八、配对成功后双方票据立即移出池子，返回行给出 parties、tickets、rating_gap 与 formed_at，pending 按入池序号返回剩余票据；九、票据自入池起等待达到 ticket_ttl 秒即过期，purge 删除并返回条数，过期票据不计入 matches，同一队伍过期后可重新入池；十、cancel 返回 cancelled 状态并移除票据，再次取消或取消已匹配票据抛 MM_UNKNOWN_TICKET；十一、get_ticket 对未登记队列抛 MM_UNKNOWN_QUEUE，对不在池中的队伍返回 None；十二、stats 恰好包含 enqueued、matches、cancelled、expired 四个计数，matches 以配对局数计，reset_stats 全部归零。

硬约束：入池、配对、取消、清理全程持同一把可重入锁，同一 party_id 被多线程同时入池只能生成一张票据、enqueued 只加一次；snapshot 必须是深拷贝，能跨进程恢复队列、票据、序号和统计，恢复后已配对票据不得复活；票据池与单次配对量都必须有上限，过期清理不得无界累积；restore 遇到版本不符、字段类型错误或票据引用未知队列时抛 MM_SNAPSHOT_INVALID 且原状态不变。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 地狱
