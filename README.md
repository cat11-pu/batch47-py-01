# SIP 事务与对话状态纠偏

## 题面

SIP 代理层这个季度被两件事反复拖累：一是重传风暴，同一 INVITE 在 200 毫秒内到达八份，代理给每份都新建了事务，下游振铃了八次；二是时序错乱，客户端用不同 branch 把 CSeq 从 7 回退到 3，代理照样转发，已经确认的对话被重新拉起。还有一批零散工单：Max-Forwards 明明是 0 的请求被继续转发，形成了环路；branch 不带 z9hG4bK 魔数的老设备请求被直接放行；ACK 在 200 之前就到了，代理当成正常确认；CANCEL 发出之后又收到 200，对话状态乱跳；注册绑定过期没有清理，来电仍然被送到已经下线的终端；事务表没有上限，压测时内存一路涨到几十 GB；接入层还反馈，OPTIONS 探活请求被当成建立对话，每探一次就多一个永远结束不了的对话，同一路呼叫的 ACK 带错 CSeq 时又被静默丢掉，主叫一直听着振铃。代码在 gsb/batch47-safety-d-05 的 sip_transaction_guard.py，类 SipTransactionGuard。

它负责请求校验、事务去重、对话状态与注册绑定。构造 SipTransactionGuard(*, max_forwards=70, max_transactions=256, default_expires=900, dialog_timeout=1800, transaction_ttl=64, clock=None)。接口：register_contact(aor, *, contact, expires=None, now=None) -> dict；lookup_contact(aor, *, now=None)；receive(message, *, now=None) -> dict；respond(dialog_id, *, status, now=None) -> dict；cancel(dialog_id, *, now=None) -> dict；ack(dialog_id, *, cseq, now=None) -> dict；terminate(dialog_id, *, now=None) -> dict；purge(now=None) -> dict；stats()、reset_stats()、snapshot()、restore(state)。错误统一抛 SipError(code, detail)。

要覆盖的故障与边界：一、aor 不以 sip: 开头或过短抛 SIP_BAD_AOR，contact 不以 sip: 开头抛 SIP_BAD_CONTACT，expires 为负或超过一天抛 SIP_BAD_EXPIRES，expires 为 0 删除绑定，删除不存在的绑定抛 SIP_UNKNOWN_BINDING；二、绑定在注册时间加有效期到期，lookup 返回 None 并顺手清除；三、请求缺 call_id、from_tag、cseq、max_forwards，或 cseq 不是正整数，抛 SIP_MALFORMED；四、方法不在允许集合抛 SIP_METHOD_NOT_ALLOWED，branch 不以 z9hG4bK 开头抛 SIP_BAD_BRANCH，max_forwards 为 0 或超过实例上限抛 SIP_MAX_FORWARDS；五、事务键由 call_id、branch、方法、cseq 组成，命中即返回 retransmission，不计入 accepted；六、同一 call_id 的 CSeq 必须严格递增，回退或相等且非重传抛 SIP_CSEQ_OUT_OF_ORDER；七、INVITE 建对话进入 early，同一对话再收到 INVITE 抛 SIP_DUPLICATE_DIALOG，应答的 1xx 保持 early，2xx 进入 confirmed，3xx 及以上进入 failed，对 confirmed 再次应答抛 SIP_ALREADY_CONFIRMED，状态码不在 100 到 699 抛 SIP_BAD_STATUS；八、ACK 只允许在 confirmed 上，early 上抛 SIP_DIALOG_EARLY，cseq 必须等于 INVITE 的 cseq 否则抛 SIP_CSEQ_OUT_OF_ORDER，重复 ACK 抛 SIP_DUPLICATE_ACK；九、CANCEL 只在 early 有效，confirmed 上抛 SIP_CANCEL_TOO_LATE，已取消或已终止抛 SIP_DIALOG_TERMINATED；十、BYE 把对话置为 terminated，二次 BYE 抛 SIP_DIALOG_TERMINATED，未知对话抛 SIP_UNKNOWN_DIALOG；十一、事务表达到 max_transactions 抛 SIP_TRANSACTION_OVERFLOW，事务按 transaction_ttl 过期清理，对话按 dialog_timeout 清理；十二、snapshot/restore 覆盖绑定、对话、事务与 CSeq 历史，非法结构抛 SIP_SNAPSHOT_INVALID 且原状态不变。

硬约束：事务表、对话表与绑定表必须持同一把锁，八个线程同时收同一 INVITE 只能有一次 accepted，其余全部返回 retransmission；快照必须深拷贝，恢复后事务仍按重传处理、CSeq 历史不丢；事务表与对话表按时间清理，不允许无界增长，绑定过期后必须立刻查不到而不是等下一次全量清理；stats 精确区分接受、重传、建立对话与终止对话，reset_stats 归零。

验收：python3 -m pytest -q tests 的 15 条必须全部通过；不得修改测试、跳过或用固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 地狱
