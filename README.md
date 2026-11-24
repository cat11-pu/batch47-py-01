# 任务题面

## 题面

情人节活动上线后，送礼每天产生上百条客服单。有人连点三次赠送，对方收到三份礼物，点券也扣三次；有人用同一订单号重发，系统又生成一份；被拉黑的人换个入口继续送礼，收件箱照样出现。每日额度按角色算而非账号，多开账号能绕过上限；同一对好友一天收七八份，界面却显示最多三份。收件箱堆满后新礼物被丢弃，发件人点券不退；礼物过期后既不退回也不提示，玩家拿截图来要钱。领取环节更乱：礼物能被非收件人领走；同一礼物被两个客户端同时领取，亲密度加两次；过期礼物在领取时才报错，点券却已经退了两次。亲密度在两个方向上还对不上，A 看是 30，B 看是 0。维护重启后待领礼物、黑名单和当日额度全部丢失，客服只能人工补发。

范围是 gsb/batch47-game-b-04 下的 social_gift_guard.py，类 SocialGiftGuard，负责礼物目录、玩家钱包、黑名单、赠送、领取、过期退回和亲密度。公开接口：`SocialGiftGuard(*, daily_send_limit=20, pair_daily_limit=3, gift_ttl=3600, max_pending=500, max_intimacy=1000)`；`register_gift(gift_id, *, name, cost, intimacy) -> dict`；`register_player(player_id, *, server, wallet=0) -> dict`；`topup(player_id, amount) -> dict`；`wallet(player_id) -> int`；`block(player_id, blocked_id) -> dict`；`unblock(player_id, blocked_id) -> dict`；`send(sender_id, recipient_id, gift_id, *, now=None, request_id=None) -> dict`；`claim(recipient_id, instance_id, *, now=None) -> dict`；`pending(recipient_id, *, now=None) -> list`；`purge(*, now=None) -> int`；`intimacy(first_id, second_id) -> int`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 GiftError(code, detail)。

故障与边界：一、礼物定义要求名称非空、价格为正整数、亲密度非负整数，同一 gift_id 完全一致时幂等返回，任一项不同抛 GIFT_CONFLICT，非法定义抛 GIFT_INVALID_GIFT；二、玩家档案重复登记同服同余额幂等返回，换服抛 GIFT_SERVER_MISMATCH，余额不同抛 GIFT_PROFILE_CONFLICT；三、充值金额必须为正整数，否则抛 GIFT_INVALID_AMOUNT，未登记玩家统一抛 GIFT_UNKNOWN_PLAYER；四、block 任一方向后双方互赠都抛 GIFT_BLOCKED 且计一次 blocked，黑名单可解除，重复解除抛 GIFT_NOT_BLOCKED，自己拉黑自己抛 GIFT_SELF_BLOCK；五、send 校验发件人、收件人、礼物存在，自己送自己抛 GIFT_SELF，被拉黑抛 GIFT_BLOCKED 且不扣钱不生成实例；六、同一 request_id 以相同发件人、收件人、礼物重发返回原实例且不重复扣款、不重复计数，换参数复用抛 GIFT_REQUEST_REUSED；七、每个发件人按 UTC 自然日限制 daily_send_limit 次，每对发件收件人按日限制 pair_daily_limit 次，越界分别抛 GIFT_DAILY_LIMIT、GIFT_PAIR_LIMIT，额度在失败时不得消耗；八、全池待领礼物达到 max_pending 时先清理过期礼物，仍满抛 GIFT_BOX_FULL，余额不足抛 GIFT_INSUFFICIENT_FUNDS，两种情况都不得生成实例；九、send 成功才扣发件人钱包，实例记录 created_at 与 expires_at，pending 只返回本人待领礼物并按生成顺序排序；十、claim 只允许收件人本人，其他人抛 GIFT_NOT_RECIPIENT，重复领取抛 GIFT_ALREADY_CLAIMED，未知实例抛 GIFT_UNKNOWN_INSTANCE；十一、礼物等待达到 gift_ttl 秒即过期，领取或 purge 命中过期时把点券退回发件人一次并抛 GIFT_EXPIRED 或计入返回值，之后的领取继续抛 GIFT_EXPIRED，不得二次退款；十二、领取成功按礼物亲密度累加双方对称亲密度并以 max_intimacy 封顶，stats 恰好包含 sent、claimed、expired、blocked 四个计数，reset_stats 归零。

硬约束：赠送、领取、清理、拉黑共用一把可重入锁，八个线程同时领取同一份礼物只能有一个成功、其余全部按 GIFT_ALREADY_CLAIMED 拒绝，亲密度只加一次；snapshot 必须深拷贝礼物目录、钱包、黑名单、待领实例、request 映射、亲密度、每日与每日对额度、统计，跨进程恢复后待领礼物仍可领取、过期仍会退款、额度继续累计，已领取或已退回的礼物不得复活；每日总额度、每对额度、收件箱容量、礼物有效期四个边界都必须拒绝越界而不是截断；restore 遇到版本不符、字段类型错误、request 映射与实例不一致、黑名单引用未登记玩家时抛 GIFT_SNAPSHOT_INVALID，原状态保持不变。时间参数接受带时区的 ISO 8601 字符串，缺省取当前 UTC 时间。

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
