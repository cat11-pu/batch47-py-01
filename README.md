# GSB 任务说明

## 题面

本周运营转来一叠工单，都指向同一套邮箱逻辑：跨服补给邮件点领取没有反应；同一封到付邮件被连点两次，附件进了两次背包；邮箱塞满后补发邮件直接丢失；已经过期的邮件仍能领取；到付余额不足时玩家反复点击，附件一直挂在邮箱里；批量领取里一封到付邮件失败，后面的邮件全部停住；删除未领取附件的邮件后客服无法恢复；服务器滚动重启后，刚领过的邮件又能再领一次。范围是 gsb/batch47-game-a-01 下的 mail_attachment_service.py，类 MailAttachmentService，负责邮箱槽位、附件合并、到付结算、过期清理和状态快照。

公开接口（Python 类型注解）：`MailAttachmentService(*, slot_limit=50, attachment_limit=3, batch_limit=20, default_ttl=86400, clock=None)`；`credit(player: str, amount: int) -> int`；`balance(player: str) -> int`；`deliver(mail_id: str, *, recipient: str, sender: str, subject: str, attachments, cod_fee: int = 0, expires_at=None, now=None) -> dict`；`claim(mail_id: str, *, player: str, now=None) -> dict`；`claim_all(*, player: str, now=None) -> dict`；`delete(mail_id: str, *, player: str, now=None) -> dict`；`status(mail_id: str, *, now=None) -> dict`；`list_mailbox(*, player: str, now=None) -> list`；`purge_expired(now=None) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 MailError(code, detail)。

故障与边界：一、同一 mail_id 重复投递且收件人、发件人、标题、附件、到付金额与过期时间完全一致时返回 status=duplicate，不占新槽位也不增加 delivered 计数，任一字段不同抛 MAIL_CONFLICT；二、投递前先清理过期邮件，清理后仍达到 slot_limit 抛 MAILBOX_FULL，且这封邮件不得写入；三、附件按 item 合并计数，数量必须为正整数、item 非空，去重后的物品种类超过 attachment_limit 抛 MAIL_TOO_MANY_ATTACHMENTS，单条非法抛 MAIL_BAD_ATTACHMENT；四、claim 对未知或已删除的邮件抛 MAIL_NOT_FOUND，非收件人抛 MAIL_NOT_OWNER；五、now 大于等于 expires_at 即判过期抛 MAIL_EXPIRED，早一秒仍可领取；六、已领取的邮件再次 claim 返回 status=already_claimed、code=MAIL_ALREADY_CLAIMED，绝不重复发附件或重复扣款；七、到付金额大于零时先检查余额，不足抛 MAIL_COD_INSUFFICIENT 且邮件保持未领取、余额不变，成功领取只扣一次并在结果里给出 cod_paid；八、claim_all 按投递序号升序逐封处理，待领邮件数超过 batch_limit 抛 MAIL_BATCH_TOO_LARGE，单封失败只记一行 rejected 并继续后面的邮件；九、delete 仅允许无附件或已领取的邮件，否则抛 MAIL_HAS_ATTACHMENTS，删除后再操作按 MAIL_NOT_FOUND 处理；十、purge_expired 返回清理数量并计入 expired，list_mailbox 不返回过期邮件且按投递顺序排列，已领取的邮件显示 claimed；十一、snapshot 深拷贝余额、邮件、序号与统计，restore 恢复后历史领取仍算已领取，结构非法抛 MAIL_SNAPSHOT_INVALID 且原状态不变；十二、stats 精确区分 delivered、claimed、duplicates、expired、rejected，reset_stats 全部归零。

硬约束：claim 全程持锁，多线程同时领取同一封到付邮件只能有一个 claimed，其余 already_claimed，余额只扣一次；快照跨进程恢复后继续领取不重复发放；邮箱按过期时间回收槽位，不允许无限增长；清理过期邮件与投递、领取必须互斥，不能出现同一封邮件既被删除又被领取，也不能把未过期邮件误删。

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
