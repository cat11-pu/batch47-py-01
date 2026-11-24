# GSB 任务说明

## 题面

春节公会红包出了事故：会长发 10 金币 3 个包，最后一共领出 12 金币；同一玩家连点两次拿到两份；没入会的玩家也能领；红包过期后剩余金币没有退回；会长在无人领取时撤回，退款却重复到账；并发领取时最后一个名额被发超；服务器重启后红包余额和领取名单对不上。范围是 gsb/batch47-game-a-05 下的 guild_red_packet_pool.py，类 GuildRedPacketPool，负责公会归属、金币托管、确定性拆分、领取、撤回与过期退款。

公开接口（Python 类型注解）：`GuildRedPacketPool(*, max_packets=20, ttl=600, clock=None)`；`join_guild(player: str, guild: str) -> dict`；`credit(player: str, coins: int) -> int`；`balance(player: str) -> int`；`create_packet(packet_id: str, *, guild: str, sender: str, total_coins: int, slots: int, now=None) -> dict`；`claim(packet_id: str, *, player: str, now=None) -> dict`；`revoke(packet_id: str, *, sender: str, now=None) -> dict`；`expire(now=None) -> int`；`status(packet_id: str) -> dict`；`list_packets(guild=None) -> list`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 PacketError(code, detail)。

故障与边界：一、create_packet 要求 total_coins 大于等于 slots 且 slots 至少为 1，否则抛 PACKET_INVALID 或 PACKET_BAD_REQUEST；发起人必须属于该公会，否则抛 PACKET_NOT_MEMBER；余额不足抛 PACKET_INSUFFICIENT_BALANCE；创建时一次性扣走全额托管；二、拆分完全确定：base 为总额整除份数，extra 为余数，领取序号小于 extra 的拿 base 加 1，其余拿 base，同样的领取顺序必须得到同样的金额；三、同一 packet_id 重复创建且公会、发起人、总额、份数一致时返回 status=duplicate，不重复扣款也不增加 created，任一参数不同抛 PACKET_CONFLICT；四、发起人自己领抛 PACKET_SENDER，非本公会成员抛 PACKET_NOT_MEMBER，同一玩家重复领取返回 already_claimed、code=PACKET_ALREADY_CLAIMED 与原金额，余额不再增加；五、名额领完后再领返回 status=empty、code=PACKET_EMPTY，红包状态为 exhausted；六、now 大于等于过期时间时领取抛 PACKET_EXPIRED，同时把剩余金币退回发起人并置 expired，expire 扫描返回本次过期数量，重复扫描为 0，退款只发生一次；七、revoke 仅发起人可调用且只在无人领取时生效，退回全部托管并置 revoked，已经有人领取抛 PACKET_LOCKED，重复撤回幂等且不得二次退款；八、活跃红包数量受 max_packets 限制，超出抛 PACKET_CAPACITY，已领完、已过期、已撤回的红包不占名额；九、任何时刻总额等于已领金额、红包剩余与已退金额之和，重启与过期都不能凭空造币；十、status 返回 state、claimed、remaining、coins_left、refunded 与 claims，list_packets 按创建顺序返回并可按公会过滤；十一、snapshot 深拷贝余额、公会关系、红包与统计，restore 后领取序号与金额继续衔接，快照之后创建的红包不出现，结构非法抛 PACKET_SNAPSHOT_INVALID 且原状态不变；十二、多线程同时领取 3 个名额的红包，只能有 3 人成功，金额合计等于总额，其余返回 empty。

硬约束：领取与退款在同一把锁内完成名额判断、扣减与入账，同一玩家并发点击只能到账一次；快照跨进程恢复后剩余金额、领取名单与已退金额必须自洽；活跃红包数量和个人领取次数都有硬上限，不允许超额发放。

验收：`python3 -m pytest -q tests` 的 15 条必须全部通过；不得修改测试、跳过或按固定输入写死；并发领取与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 困难
