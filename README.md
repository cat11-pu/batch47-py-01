# GSB 网络与通信安全任务

## 题面

大促前夜网关的 TCP 半连接队列被打满：一个 /24 里的肉鸡用几千个伪造源端口发 SYN，正常用户的握手全排在后面超时；同一台客户端重传 SYN 被当成新连接，半连接计数翻倍，监控告警连响；负载均衡后面某个 VIP 的 backlog 只剩 1，运维发现新连接全被丢弃；上游 ACK 序号对不上也照样建连；攻击者把一分钟前的 cookie 重放回来仍然通过；网关滚动重启后半连接表和 cookie 记录全丢，压测流量重新打满；被拉黑的来源换个端口继续发 SYN 又进了队列。

代码位置是 gsb/batch47-safety-c-03 下的 syn_admission_guard.py，类型 SynAdmissionGuard，承担监听端口与来源地址的半连接管理，超限时改用无状态 cookie 挑战，并记录建连统计。公开接口：SynAdmissionGuard(*, max_half_open=256, per_source_limit=8, syn_timeout=15, established_timeout=300, cookie_secret=b"syn-admission-default", cookie_window=60, clock=None)；register_listener(port, *, backlog=128)；unregister_listener(port)；syn(src_ip, src_port, dst_port, *, seq, now=None)；ack(src_ip, src_port, dst_port, *, ack, cookie=None, now=None)；reset(src_ip, src_port, dst_port, *, now=None)；half_open_count(dst_port=None)；established_count(dst_port=None)；is_established(src_ip, src_port, dst_port, *, now=None)；block_source(ip, *, reason="manual")；unblock_source(ip)；is_blocked(ip)；purge(now=None)；snapshot()；restore(state)；stats()；reset_stats()。失败抛 SynGuardError(code)。

故障点与边界条件：一、监听端口 1 到 65535，非法抛 SYN_INVALID_PORT，重复注册同 backlog 幂等，不同 backlog 抛 SYN_LISTENER_CONFLICT；来源地址、源端口、序号、ACK 非法抛 SYN_INVALID；未注册目标端口抛 SYN_UNKNOWN_LISTENER。二、每次合法 syn 调用都计入 stats 的 syn；命中已建连元组返回 action 为 established 且 already 为真。三、同一元组重复 SYN 且序号相同返回 action 为 retransmit，不新增半连接；序号不同抛 SYN_SEQ_CONFLICT，原半连接保留。四、单源半连接数达到 per_source_limit、全局达到 max_half_open、或该监听端口达到 backlog 时返回 action 为 challenge，并给出 32 位十六进制 cookie，此时不建半连接。五、cookie 由来源地址、源端口、目标端口、序号和 cookie_window 派生；有效期两个窗口；ack 带 cookie 时校验元组与 HMAC，伪造、元组不符或 ack 不等于序号加一都抛 SYN_COOKIE_INVALID，过期抛 SYN_COOKIE_EXPIRED，重复使用抛 SYN_COOKIE_REPLAY，成功返回 status 为 established、via 为 cookie。六、普通 ack 必须命中未过期半连接，否则抛 SYN_UNKNOWN_HANDSHAKE；ack 不等于序号加一抛 SYN_ACK_MISMATCH 且保留半连接。七、建连成功返回 status 为 established、via 为 buffer；对已建连元组重复 ACK 返回 already 为真且不重复计 established。八、reset 删除该元组的半连接或已建连状态，删到才算真。九、syn_timeout 内未完成握手的半连接、established_timeout 内无活动的已建连状态都由 purge 清掉，到期时刻即算超时，返回清理条数。十、block_source 返回 ip、blocked、reason，之后该源 syn 返回 action 为 drop、reason 为 blocked 且不占半连接；unblock_source 返回是否真的解除。十一、snapshot 返回 version、listeners、half_open、established、blocked、cookies、stats，版本为 1，必须是深拷贝；restore 结构非法抛 SYN_SNAPSHOT_INVALID 且原状态不变。十二、stats 固定六个键 syn、accepted、retransmit、challenged、dropped、established，reset_stats 全部归零；半连接数与已建连数量方法只统计当前表内条目。

硬约束：syn、ack、拉黑、清理与快照全程持锁，8 个线程同时为同一元组发相同 SYN 只能有一个 accept、其余 retransmit，半连接只有一条；快照跨进程恢复后监听端口、半连接、已建连、cookie 与黑名单都能继续使用，恢复前后统计一致；任何路径下半连接总数不超过 max_half_open、单源不超过 per_source_limit、单监听端口不超过 backlog。now 接受 ISO 8601 字符串，缺省取当前 UTC 时间。

验收方式：`python3 -m pytest -q tests` 15 条全部通过；不得修改或跳过测试、不得针对固定输入写死；并发与恢复要自行验证；不涉及外部依赖，只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 困难
