# GSB 网络与通信安全任务

## 题面

边缘路由器最近三天被同一类分片打穿：首片只有 8 字节、后续片偏移乱跳的分片流绕过 ACL 进了内网；两台设备发出的重叠分片让重组结果和防火墙看到的不一致；一个超长分片组把重组队列占满，新流量全部被丢；攻击者把源地址分散到几十个网段，单条流看起来都正常；设备重启后未完成的分片组全丢，同一个标识的分片在重启后又从零开始；监控还发现完成过的标识仍能继续收分片，重复数据被上层当成新报文。

本次改动落在 gsb/batch47-safety-c-02 的 frag_reassembly_guard.py，主类型 FragGuard：按源、目的、标识、协议追踪分片组，判定重叠和越界，限制资源并隔离攻击源。公开接口：FragGuard(*, max_datagrams=1024, max_fragments_per_datagram=64, reassembly_timeout=30, min_fragment_payload=8, max_datagram_bytes=65535, auto_block_threshold=2, clock=None)；datagram_key(fragment)；submit(fragment, *, now=None)；pending_count()；list_pending()；flush(key)；block_source(ip, *, reason="manual")；unblock_source(ip)；is_blocked(ip)；expire(now=None)；snapshot()；restore(state)；stats()；reset_stats()。失败抛 FragError(code)。

需要覆盖的故障与边界：一、fragment 必须含 src、dst、ident、protocol、offset、payload_len、more_fragments，字段缺失、地址非法或协议越界抛 FRAG_INVALID。二、offset 必须是 8 的倍数，且 offset 加 payload_len 不超过 max_datagram_bytes，否则抛 FRAG_INVALID_OFFSET。三、more_fragments 为真时 payload_len 不得小于 min_fragment_payload，否则抛 FRAG_TOO_SMALL；最后一片不受此限。四、同一分片组内字节区间不得重叠；完全相同的区间且 digest 相同算重复提交，返回 status 为 duplicate 且不重复计数，digest 不同抛 FRAG_OVERLAP。五、单片组分片数超过 max_fragments_per_datagram 抛 FRAG_TOO_MANY，并丢弃该组已有上下文。六、收到带最后一片标记的分片后，只有 0 到 final_end 全覆盖且无空洞才返回 status 为 complete 和 bytes 为 final_end，否则 status 为 buffered；乱序到达也要能重组；submit 返回 status、key、datagram_id、fragments，重组完成时另带 bytes。七、同一标识完成后在 reassembly_timeout 内再收到分片抛 FRAG_AFTER_COMPLETE，保留期过后可重新开始。八、未完成组数达到 max_datagrams 时先清理超时组，仍满抛 FRAG_TABLE_FULL 且不建新组。九、submit 先检查源地址黑名单，命中抛 FRAG_SOURCE_BLOCKED；同一源累计违规达到 auto_block_threshold 自动拉黑，unblock_source 解除。十、expire 删除 first_seen 加 reassembly_timeout 已到的未完成组并返回条数，到期时刻即算超时，完成记录的保留期清理不计数。十一、list_pending 按 key 排序，每行给出 key、src、dst、ident、protocol、fragments、final_end；flush 按 key 删组并返回布尔值。十二、snapshot 返回 version、pending、completed、blocked、stats，pending 的 key 由 datagram_key 生成，值含 src、dst、ident、protocol、first_seen、final_end、fragments，必须是深拷贝；restore 结构非法抛 FRAG_SNAPSHOT_INVALID 且原状态不变。stats 固定五个键 fragments、completed、duplicates、violations、dropped，fragments 只计真正入组的分片，duplicates 单独计，dropped 只计黑名单和表满拒收，reset_stats 全部归零。

硬约束：submit、拉黑、清理和快照全程持锁，8 个线程同时提交同一组的 8 个互不重叠分片只能有一个 complete、其余 buffered；快照跨重启恢复后待重组组、黑名单和统计保持一致；任意路径下待重组组数不超过 max_datagrams，单片组分片数不超过上限。now 接受 ISO 8601 字符串，缺省取当前 UTC 时间。

交付前运行 `python3 -m pytest -q tests`，15 条测试必须全部通过；测试文件不得改动、跳过或按固定输入写死；并发与恢复场景自行验证；只在本地完成实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 地狱
