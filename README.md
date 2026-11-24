# GSB 业务任务

## 题面

拍卖行改版之后，寄售邮件出了大乱子：买家点开一封带附件的邮件，网络抖动连点了三次，武器进了背包三把，卖家只收到一份手续费；有人用低于对方报价的余额点领取，界面直接白屏，附件卡在邮件里谁也拿不到；寄售物品设置了七天有效期，到期后邮件条目消失，附件没有回到卖家手里，客服只能按截图赔钱；邮箱容量写的是五十封，实际发到第七十封仍然能塞进去，随后查询接口超时；同一个邮件 ID 被补偿脚本重复投递，两次都创建成功。问题集中在 gsb/batch47-game-a-08 的 mail_cod_delivery.py，核心类 CodMailbox，需要把附件托管、到付手续费、配额和过期退件重新串起来。

公开接口（Python 类型注解）：`CodMailbox(*, quota: int = 50, ttl: int = 86400, max_attachments: int = 5, batch_limit: int = 20, clock=None)`；`grant_currency(player: str, amount: int) -> int`；`balance(player: str) -> int`；`grant_items(player: str, item_id: str, count: int) -> dict`；`inventory(player: str) -> dict`；`send(sender: str, recipient: str, *, attachments=None, currency: int = 0, cod_fee: int = 0, mail_id: str | None = None, now=None) -> dict`；`mailbox(player: str, *, now=None) -> list`；`claim(mail_id: str, *, player: str, now=None, request_id: str | None = None) -> dict`；`claim_batch(player: str, mail_ids, *, now=None) -> list`；`expire(now=None) -> int`；`returned(player: str) -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 MailError(code, detail)，code 用下面的字符串。

故障与边界：一、发送时附件立刻从发件人背包扣除进入托管，发件人数量不足抛 MAIL_INSUFFICIENT_ITEMS，邮件不得创建，成功返回 mail_id、sender、recipient、attachments、cod_fee、created_at、expires_at、status 为 unclaimed 等字段；二、发件人与收件人相同、附件和附币都为空、cod_fee 为负都抛 MAIL_INVALID，附件条目数超过 max_attachments 抛 MAIL_TOO_MANY_ATTACHMENTS，mail_id 已存在抛 MAIL_DUPLICATE；三、邮件的 expires_at 等于创建时间加 ttl，now 大于等于该值即视为过期；四、领取前先做过期判断，过期邮件的物品进入发件人的 returned 汇总（返回 item_id 到数量的映射），附带的金币退回发件人余额，并抛 MAIL_EXPIRED；五、quota 按收件人未领取邮件数计算，发送前先清理该收件人的过期邮件，仍然达到上限抛 MAIL_QUOTA 且不扣发件人任何物品；六、领取只允许收件人本人，其他人抛 MAIL_NOT_RECIPIENT，邮件不存在抛 MAIL_UNKNOWN，已领完再领抛 MAIL_ALREADY_CLAIMED，未持有任何物品的玩家 inventory 返回空字典；七、到付手续费只在第一次成功领取时扣一次，余额不足抛 MAIL_INSUFFICIENT_FUNDS 且手续费、附件、邮件状态全部不变；八、领取成功后附件与附币转入收件人，手续费转入发件人，返回 mail_id、status 为 claimed、items、currency、fee；九、带 request_id 的领取必须幂等，同一请求重放返回首次结果不再转账，同一 request_id 换收件人抛 MAIL_REQUEST_CONFLICT；十、claim_batch 单次最多 batch_limit 封，超出抛 MAIL_BATCH_TOO_LARGE，逐封处理并按入参顺序返回结果，成功行给 mail_id 与 status 为 claimed，失败行给 mail_id、status 为 rejected 与 code，单封失败不影响其他邮件；十一、expire 返回本次过期封数，重复调用不得重复退件，mailbox 按创建时间升序返回未领取邮件；十二、snapshot 返回 version 为 1、包含 players、mails、stats 的深拷贝，players 下每个玩家含 items、balance、returned，stats 返回 sent、claimed、expired、returned_units，restore 遇到结构或数值非法抛 MAIL_SNAPSHOT_INVALID 且原状态不变，reset_stats 全部归零。

硬约束：领取、退回和余额变动必须在同一把锁内完成，多线程同时领取同一封邮件只能有一个成功，其余抛 MAIL_ALREADY_CLAIMED，手续费只能扣一次；附件托管必须守恒，任何失败路径都要保证物品不会凭空多出或消失；过期清理与配额检查按收件人索引推进，不允许每次操作全量扫描所有邮件。

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
