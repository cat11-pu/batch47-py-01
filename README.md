# 题面

园区的 CoAP 设备管理平台接入了两千多台温湿度传感器，最近一个月值班表上反复出现三类问题。第一类是观察关系错乱：设备重启后同一个 token 重复注册，平台给一台设备保留了两条关系，通知发两遍，界面上的曲线出现锯齿；第二类是过期通知覆盖新值，设备侧序号回退的报文被照单全收，运维看到温度从二十六度跳回十九度；第三类是大报文分块重组：固件上报走的 Block2 分块在网关多实例之间乱序到达，拼接出来的配置块中间缺了一段，校验和永远对不上。另外，某台被入侵的设备用同一个 Message ID 每秒重放几十次，每次都被当成新消息处理；某个网段用脚本批量 observe 了几万个资源路径，观察表把进程内存吃满；平台滚动升级后，未确认通知和半截分块全部丢失，设备迟迟收不到补发。

范围是 gsb/batch47-safety-d-10 下的 coap_observe_guard.py，类 CoapObserveGuard。公开接口（Python 类型注解）：`CoapObserveGuard(*, exchange_lifetime: int = 247, max_observations: int = 32, max_relations: int = 8, max_block_size: int = 1024, max_body_bytes: int = 65536, max_pending: int = 4, clock=None)`；`register_resource(path: str, *, max_age: int = 60) -> dict`；`observe(*, endpoint: str, token: str, path: str, now=None) -> dict`；`cancel_observe(*, endpoint: str, token: str, path: str) -> dict`；`notify(*, path: str, seq: int, payload_len: int, now=None) -> dict`；`ack(*, endpoint: str, token: str, path: str, seq: int, now=None) -> dict`；`describe(*, endpoint: str, token: str, path: str) -> dict`；`incoming(*, endpoint: str, message_id: int, token: str, payload, block=None, now=None) -> dict`；`sweep(*, now=None) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 CoapError(code, detail)。

故障与边界：一、资源路径必须以斜杠开头，不允许连续斜杠、结尾斜杠、空白与问号井号，否则抛 COAP_BAD_PATH；同名资源换 max_age 再登记抛 COAP_RESOURCE_CONFLICT，完全一致幂等返回 created 为 False；二、token 必须是二至十六位偶数长度十六进制串，否则抛 COAP_BAD_TOKEN，endpoint 为空抛 COAP_BAD_ENDPOINT，未登记路径抛 COAP_UNKNOWN_RESOURCE；三、观察关系以 endpoint、token、path 三元组唯一，重复 observe 幂等返回 existing 为 True 与当前最后序号，单 endpoint 关系数超过 max_relations 抛 COAP_RELATION_QUOTA，全局关系数超过 max_observations 抛 COAP_OBSERVE_LIMIT；四、notify 的 seq 必须是正整数且严格大于该路径上一次成功通知的序号，重复或回退抛 COAP_STALE_NOTIFICATION 且不建立待确认记录，payload_len 为负抛 COAP_BAD_PAYLOAD、超过 max_body_bytes 抛 COAP_BODY_TOO_LARGE；五、通知按 endpoint 字典序投递并返回 delivered 与 suppressed 两个列表，某关系待确认数达到 max_pending 时进入 suppressed 且不推进该关系的最后序号；六、ack 必须命中该关系当前待确认的 seq，否则抛 COAP_UNKNOWN_NOTIFICATION，seq 非正整数抛 COAP_BAD_SEQ，关系不存在抛 COAP_NO_RELATION；七、cancel_observe 返回被丢弃的待确认条数并删除关系，重复取消抛 COAP_NO_RELATION；八、incoming 按 endpoint 与 Message ID 去重，exchange_lifetime 内重复返回 status 为 duplicate 与 original_status，并计入 duplicates，不抛异常、不重复写缓冲，超过生存期后同一 Message ID 允许重新处理；九、单包消息立即完成，payload 超过 max_body_bytes 抛 COAP_BODY_TOO_LARGE，非 bytes 抛 COAP_BAD_PAYLOAD，Message ID 非正整数抛 COAP_BAD_MESSAGE_ID；十、分块消息的 size 必须是十六到 max_block_size 之间二的幂，否则抛 COAP_BAD_BLOCK，第一块 num 必须为 0、后续块的偏移必须恰好等于已收字节数，否则抛 COAP_BLOCK_OUT_OF_ORDER，中途改 size 抛 COAP_BLOCK_SIZE_CHANGED，累加超过 max_body_bytes 抛 COAP_BODY_TOO_LARGE 并丢弃该次重组；十一、分块以 endpoint 与 token 标识同一次传输，more 为真的块长度必须等于 size，最后一块允许短于 size，完成后返回完整 body 并增加 blockwise_completed；十二、sweep 清理超过 exchange_lifetime 的去重记录与半截分块并返回被清理的去重条数；十三、snapshot 必须深拷贝资源、关系、待确认序号、各路径最大序号、去重表与半截分块，restore 后旧 Message ID 仍判重复、半截分块能接着拼、未确认通知不丢，结构非法抛 COAP_SNAPSHOT_INVALID 且原状态不变。

硬约束：observe、notify、ack 与 incoming 全程持锁，八个线程同时对同一三元组 observe 只能创建一条关系，八个线程同时投递同一个 Message ID 只能有一个完成、其余全部按重复处理；notify 的序号判断必须原子，不能出现两条并发通知都通过比较；半截分块必须受 max_body_bytes 与 max_block_size 双层限制，不允许因为缺最后一块而无界增长；快照要能跨进程恢复并保持已收块数。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 困难
