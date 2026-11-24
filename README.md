# GSB 业务任务

## 题面

交易窗口上线自助交易后，诈骗申诉量翻了三倍：骗子先把稀有武器放进窗口，等对方点确认的瞬间撤下换成白装，系统仍然完成结算；两名玩家在语音里倒数同时点确认，服务器把同一件装备各自发了一份，等于凭空复制；有人点了取消，装备没有回到背包，客服只能手工补发；还有玩家挂机半小时回来，交易窗口早已超时，物品却一直锁在托管里取不出来；同一件材料可以同时挂进两笔交易，两边都点确认时只有一笔成功，另一笔把对方金币扣了却没收到货。问题集中在 gsb/batch47-game-a-07 的 player_trade_escrow.py，核心类 TradeEscrow，产品要求把托管、双确认和超时释放全部做对。

公开接口（Python 类型注解）：`TradeEscrow(*, max_items: int = 6, max_currency: int = 100000, ttl: int = 300, clock=None)`；`grant_items(player: str, item_id: str, count: int) -> dict`；`grant_currency(player: str, amount: int) -> int`；`inventory(player: str) -> dict`；`balance(player: str) -> int`；`open_trade(trade_id: str, *, initiator: str, partner: str, now=None) -> dict`；`offer(trade_id: str, player: str, *, items=None, currency: int = 0, now=None) -> dict`；`confirm(trade_id: str, player: str, *, now=None) -> dict`；`cancel(trade_id: str, *, player: str | None = None, now=None) -> dict`；`status(trade_id: str, *, now=None) -> dict`；`expire(now=None) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 TradeError(code, detail)，code 用下面的字符串。

故障与边界：一、开交易时发起方与对方相同抛 TRADE_SELF，trade_id 已出现过抛 TRADE_DUPLICATE（无论该交易当前处于什么状态都不允许复用），成功返回 trade_id、initiator、partner、status 为 open、expires_at 等于 now 加 ttl 五项；二、报价立刻进入托管，玩家可用背包与余额同步扣减，inventory 只返回数量为正的物品，取消或超时后原样退回，取消已完成的交易抛 TRADE_STATE；三、替换报价必须先释放旧托管再校验新报价，新报价超出可用数量抛 TRADE_INSUFFICIENT_ITEMS，超出余额抛 TRADE_INSUFFICIENT_CURRENCY，成功返回 trade_id、player、items、currency、status 与 confirmed；四、单笔报价的物品条目数不得超过 max_items，否则抛 TRADE_OFFER_TOO_LARGE；报价金币不得超过 max_currency，否则抛 TRADE_CURRENCY_LIMIT；五、非交易参与方操作抛 TRADE_NOT_PARTICIPANT，trade_id 不存在抛 TRADE_UNKNOWN；六、任何一次报价变更都要清空双方的确认状态；七、同一方重复确认抛 TRADE_ALREADY_CONFIRMED，单方确认返回 trade_id、status 为 waiting、confirmed 列表；双方都确认且至少一方有报价才结算，双方都是空报价抛 TRADE_EMPTY 并清空确认；八、结算必须原子交换物品与金币，返回 trade_id、status 为 completed、confirmed 与 received，received 按玩家给出 items 和 currency，结算后交易不可再次确认或取消；九、超时按 now 大于等于创建时间加 ttl 判定，expire 返回本次过期笔数并退回托管，过期交易上的报价和确认抛 TRADE_EXPIRED，status 返回 expired；十、status 返回 trade_id、status、initiator、partner、confirmed 列表、expires_at 和双方 offers（items 与 currency），cancel 返回 trade_id 与 status 为 cancelled；十一、snapshot 返回 version 为 1、包含 players、trades、stats 的深拷贝，players 下每个玩家含 items 与 balance，restore 遇到结构或数值非法抛 TRADE_SNAPSHOT_INVALID 且原状态不变；十二、stats 返回 completed、cancelled、expired、rejected 四个计数，reset_stats 全部归零。

硬约束：所有状态变更在同一把锁内完成，双方同时确认时只能结算一次，另一方的确认返回等待状态或已完成状态，不得二次发货；托管与可用背包必须始终守恒，任何失败路径都要把已扣的物品和金币退回；交易数量与单笔报价受 max_items、max_currency 限制，过期清理按笔数推进，不允许每次操作全量扫描。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与快照恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段（不进入题面）

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 地狱
