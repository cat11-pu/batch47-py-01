# GSB 任务说明

## 题面

线下交易专区连续出现申诉：双方都点了确认，装备只到了买方；卖方改价后，买方仍按旧报价成交；交易窗口过期几分钟仍能确认；同一玩家同时开两个窗口，把同一件装备卖给了两个人；确认瞬间掉线重连，金币扣了物品没到；锁定后一方撤回物品，结算照旧成功；结算失败后窗口卡在锁定状态，玩家无法重试。范围是 gsb/batch47-game-a-03 下的 player_trade_session.py，类 TradeSessionManager，负责报价、锁定、确认、原子交换、超时和状态快照。

公开接口（Python 类型注解）：`TradeSessionManager(*, session_ttl=300, max_open=50, clock=None)`；`topup(player: str, *, coins: int = 0, items=None) -> dict`；`holdings(player: str) -> dict`；`open_session(player_a: str, player_b: str, *, session_id: str | None = None, now=None) -> dict`；`offer(session_id: str, player: str, *, items=None, coins: int = 0, now=None) -> dict`；`lock(session_id: str, player: str, *, now=None) -> dict`；`confirm(session_id: str, player: str, *, now=None) -> dict`；`cancel(session_id: str, player: str, *, now=None) -> dict`；`status(session_id: str, *, now=None) -> dict`；`expire(now=None) -> int`；`list_sessions(player=None, *, now=None) -> list`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 TradeError(code, detail)。

故障与边界：一、open_session 禁止自己与自己交易（TRADE_SELF）；session_id 已存在抛 TRADE_DUPLICATE；任一参与方已在活跃窗口抛 TRADE_BUSY；活跃窗口达到 max_open 抛 TRADE_CAPACITY；二、offer 只允许参与方调用，物品数量为正整数、物品种类不超过 10、金币非负，否则抛 TRADE_BAD_REQUEST；任何一方改动报价都清空双方锁定与确认，窗口回到 open；三、lock 要求该玩家已提交过报价（空报价也算），否则抛 TRADE_NOT_READY；双方都锁定后窗口才变为 locked；四、confirm 必须在 locked 状态调用，否则抛 TRADE_NOT_READY；同一玩家重复确认抛 TRADE_ALREADY_CONFIRMED；五、结算前一次性校验双方金币与物品是否仍足够，不足抛 TRADE_INSUFFICIENT，窗口回到 open 并清空锁定与确认，补足后可以重新锁定并完成，绝不出现只扣一方的半成交；六、双方确认后原子互换金币与物品，窗口置 completed，completed 只统计一次；已完成后再 confirm 幂等返回 completed，不再搬运任何东西；七、cancel 任一参与方都可发起，重复 cancel 幂等；在 completed 或 cancelled 上再报价、锁定、确认抛 TRADE_CLOSED；非参与方抛 TRADE_NOT_PARTICIPANT；八、会话在创建时间加 session_ttl 的整点判过期：status 到点后显示 expired，confirm 抛 TRADE_EXPIRED；expire 扫描返回本次过期数量，重复扫描为 0；九、status 返回双方报价、locked 与 confirmed 名单以及 state；list_sessions 按创建顺序返回，可按玩家过滤；十、snapshot 深拷贝持有物、全部会话（含已结束）、序号与统计，restore 后锁定与确认状态仍在，快照之后开的窗口不出现，结构非法抛 TRADE_SNAPSHOT_INVALID 且原状态不变；十一、holdings 返回防御性副本，调用方修改返回值不能影响内部持有物；十二、多线程同时确认同一窗口，只有一次结算生效，金币与物品只交换一遍。

硬约束：confirm 与结算在同一把锁内完成持有物复核和互换，失败时必须整体回退到可重试状态；快照要覆盖进行中的锁定与确认，跨进程恢复后继续交易不重复结算；活跃窗口数量与单窗口物品种类都有硬上限。

验收：`python3 -m pytest -q tests` 的 15 条要全部通过；不得修改测试、跳过或用固定输入写死；并发确认与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 困难
