# QUIC 路径验证与放大防护纠偏

## 题面

边缘节点切到 QUIC 之后，安全组连着报了三起放大攻击：攻击者伪造源地址发一个很小的 Initial，网关回了几十倍的握手包；上一版 Retry 令牌是全局固定值，换一个 IP 和端口照样能用，整片地址段被人拿着令牌刷；连接迁移只改了五元组就继续按旧预算发包，新路径完全没验证；0-RTT 早数据票据被同一个客户端重复使用，同一笔下单请求被执行两次；连接表里还有一批早已关闭的空壳，收到包仍然回确认；迁移后的连接在新路径上仍然按旧路径的已收字节算预算，等于把放大闸门又打开了一次。代码在 gsb/batch47-safety-d-03 的 quic_path_validation_guard.py，类 QuicPathValidationGuard。

它负责 Retry 令牌、路径验证、放大预算与 0-RTT 票据核销。构造 QuicPathValidationGuard(*, secret, token_ttl=600, amplification_factor=3, max_connections=1000, max_tickets=1000, clock=None)。接口：issue_token(*, peer, original_dcid, now=None) -> dict；verify_token(token, *, peer, now=None) -> dict；register_ticket(ticket_id, *, expires_at, now=None) -> dict；admit_0rtt(conn_id, *, ticket_id, now=None) -> dict；open_connection(conn_id, *, peer, now=None) -> dict；record_received(conn_id, size, *, now=None)、check_send(conn_id, size, *, now=None)、record_sent(conn_id, size, *, now=None)；validate_path(conn_id, *, peer, token=None, now=None) -> dict；migrate(conn_id, *, peer, now=None) -> dict；close_connection(conn_id, *, now=None) -> dict；purge(now=None) -> dict；stats()、reset_stats()、snapshot()、restore(state)。错误统一抛 QuicError(code, detail)。

要覆盖的故障与边界：一、令牌是带完整性校验的字符串，改动任意一个字符、换一把密钥、结构不合法都抛 QUIC_TOKEN_INVALID；二、令牌绑定 ip 与端口，换端口校验抛 QUIC_TOKEN_PEER_MISMATCH；三、令牌在签发时间加 token_ttl 的整点到期，恰好到期抛 QUIC_TOKEN_EXPIRED；四、连接标识为空抛 QUIC_BAD_CONNECTION_ID，对端缺 ip 或端口越界抛 QUIC_BAD_PEER，原始连接标识为空抛 QUIC_BAD_DCID，构造时密钥为空抛 QUIC_BAD_SECRET；五、重复连接标识抛 QUIC_DUPLICATE_CONNECTION，活跃连接达到 max_connections 抛 QUIC_CONNECTION_LIMIT；六、未验证连接可发字节数为放大系数乘已收字节减已发字节，恰好相等放行，多一个字节抛 QUIC_AMPLIFICATION_LIMIT 且计数不变；七、validate_path 缺令牌抛 QUIC_TOKEN_REQUIRED，令牌所属对端与待验证对端不一致抛 QUIC_TOKEN_PEER_MISMATCH，连接当前对端与请求对端不同抛 QUIC_PATH_UNVALIDATED；八、迁移只允许已验证连接发起，迁移到相同对端抛 QUIC_SAME_PATH，迁移后预算清零并回到未验证；九、迁移后只接受待验证对端的令牌，否则抛 QUIC_PATH_MISMATCH，验证通过后发送不再受放大预算限制；十、0-RTT 票据标识为空抛 QUIC_BAD_TICKET，未登记抛 QUIC_UNKNOWN_TICKET，过期抛 QUIC_TICKET_EXPIRED，同一票据二次核销抛 QUIC_0RTT_REPLAY，票据表满抛 QUIC_TICKET_CACHE_FULL，同一票据标识登记成不同到期时刻抛 QUIC_CONFLICT；十一、已关闭连接做收发或再次关闭抛 QUIC_CONNECTION_CLOSED，未知连接抛 QUIC_UNKNOWN_CONNECTION，字节数为负或非整数抛 QUIC_BAD_SIZE；十二、snapshot/restore 覆盖连接预算、验证状态、票据与统计，非法结构抛 QUIC_SNAPSHOT_INVALID 且原状态不变。

硬约束：连接表、预算与票据核销必须持同一把锁，八个线程同时开连接、上限为三时只能成功三个；令牌到期与票据核销记录必须能随快照跨进程恢复，恢复后旧票据仍按重放拒绝；连接表与票据表按上限与到期时间收敛，不允许无界增长，放大预算的记账必须和连接状态在同一次加锁里完成，不允许先查后加导致并发穿透；stats 精确统计开启、关闭、签发、拒绝、迁移与 0-RTT 放行，reset_stats 归零。

验收：python3 -m pytest -q tests 的 15 条必须全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 地狱
