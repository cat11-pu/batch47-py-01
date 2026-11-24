# batch47-game-b-08

## 题面

gsb/batch47-game-b-08 的公会报名系统在上周跨服团本开门时连续出事：同一个玩家在三个入口同时点报名，背包里多出两份入场凭证；公会管理把容量从 40 调到 30 之后，超卖的十个人照样能进场；有队伍报名四人却只清点出两个名额，候补列表顺序每次刷新都在跳；活动锁定后还有运营脚本往名单里塞人；玩家点完确认就退出客户端，占位凭证一直不回收，候补等到开打也没转正。问题集中在 guild_event_signup_guard.py 的 EventSignupGuard 类，负责活动登记、报名窗口、名额与候补、占位确认、容量调整和活动关闭。

公开接口（Python 类型注解）：EventSignupGuard(*, max_waitlist: int = 200, max_party: int = 5, hold_ttl: int = 900, clock=None)；create_event(event_id: str, *, name: str, capacity: int, start_at: str, signup_open_at: str, signup_close_at: str | None = None) -> dict；open_signup(event_id: str, *, now=None) -> dict；sign_up(event_id: str, player_id: str, *, party_size: int = 1, request_id: str | None = None, now=None) -> dict；cancel_signup(ticket_id: str, *, player_id: str, now=None) -> dict；check_in(ticket_id: str, *, player_id: str, now=None) -> dict；set_capacity(event_id: str, capacity: int, *, now=None) -> dict；lock_event(event_id: str, *, now=None) -> dict；cancel_event(event_id: str, *, now=None) -> dict；overview(event_id: str) -> dict；tickets(event_id: str, *, status: str | None = None) -> list；position(event_id: str, player_id: str) -> int；expire(now=None) -> int；snapshot() -> dict；restore(state: dict) -> None；stats() -> dict；reset_stats() -> None。校验失败统一抛 SignupError(code, detail)。票据记录固定为 ticket_id、event_id、player_id、party_size、status、created_at、hold_until、checked_in_at、request_id、idempotent 十个字段，sign_up 结果额外带 position（已占位为 0，候补为从 1 开始的名次），overview 返回登记字段加 confirmed、waitlisted 两个计数，取消返回 ticket_id、status、freed_seats、promoted（票号列表），关活动返回 event_id、status、cancelled_tickets。

故障与边界：一、活动编号重复抛 SIGNUP_EVENT_EXISTS，名称为空、容量非正整数或时间区间倒置抛 SIGNUP_BAD_SPEC，close 早于 open 或晚于开打同样拒绝；二、未登记活动抛 SIGNUP_UNKNOWN_EVENT，草稿状态报名抛 SIGNUP_BAD_STATE，重复 open 也抛 SIGNUP_BAD_STATE；三、报名时间早于 signup_open_at 或达到 signup_close_at 抛 SIGNUP_WINDOW_CLOSED，恰好等于关闭时刻也拒绝；四、party_size 必须是 1 到 max_party 的整数，否则抛 SIGNUP_BAD_PARTY；五、剩余名额够时整队确认，不够时整队进候补，候补按先到先得，超过 max_waitlist 抛 SIGNUP_WAITLIST_FULL；六、同一玩家已有未结束票据再报名抛 SIGNUP_DUPLICATE，但同一玩家带相同 request_id 重试返回原票据并带 idempotent 标记，票号、候补位次与 hold_until 都不变；七、取消已确认票据按整队释放名额，并只把队首能装下的候补依次转正，队首队伍装不下就整体停住，不允许越过它；八、取消候补票据后其余候补位次立即前移，取消未知票据抛 SIGNUP_UNKNOWN_TICKET，非本人操作抛 SIGNUP_NOT_OWNER，重复取消抛 SIGNUP_TICKET_CLOSED；九、只有已确认票据能签到，候补签到抛 SIGNUP_NOT_CONFIRMED，重复签到或签到后取消抛 SIGNUP_ALREADY_CHECKED_IN；十、now 到达 hold_until 时签到抛 SIGNUP_HOLD_EXPIRED，票据转为 expired 并让出名额，候补照常递补；十一、expire 扫描所有活动的占位票据，到点转 expired 并返回条数，同一时刻重复调用返回 0；十二、set_capacity 缩到低于已确认人数抛 SIGNUP_CAPACITY_BELOW_CONFIRMED，扩容从队首依次转正；锁定活动状态为 locked，此后报名与调整容量抛 SIGNUP_BAD_STATE，取消活动状态为 cancelled 并把未结束票据全部作废，重复操作抛 SIGNUP_EVENT_CANCELLED。

硬约束：sign_up、cancel_signup、check_in、expire 全程持锁，八线程同时抢两个名额只能产生两张 confirmed 票据，其余全部候补；同一 request_id 的并发重试只能生成一张票据；snapshot 深拷贝活动、票据、候补队列、请求索引、计数器和票号游标，键为 version/events/tickets/queues/counters/next_id，restore 后重放仍被识别、候补顺序与统计原样恢复、新票号不与快照内票据重复，结构非法抛 SIGNUP_SNAPSHOT_INVALID 且原状态不变；占位票据不得无界堆积，max_waitlist 与 max_party 是硬上限，tickets 与 position 只读查询不得改变状态。

验收：python3 -m pytest -q tests 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 困难
