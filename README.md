# GSB 网络与通信安全任务

## 题面

数据中心接入层的 ARP 表这两周反复出事：核心网关的 IP 被同网段主机冒领，整柜流量绕到攻击者机器上；一台测试机换过网卡回插原 IP，表里旧记录还在，业务时通时断；运维按工单加了静态白名单，第二天发现被动态学习覆盖；某个端口连续发十几次伪造 ARP，交换机只在日志里刷字，端口照常转发；上联口看到网关自己的 MAC 从接入口冒出来，也没人拦。

交付范围限定在 gsb/batch47-safety-c-01 下的 arp_binding_guard.py，核心类型 ArpBindingGuard，职责是按 VLAN 维护 IP/MAC/端口绑定、检查入向 ARP 报文并隔离问题端口。公开接口：ArpBindingGuard(*, binding_ttl=300, max_bindings=4096, violation_threshold=3, quarantine_seconds=300, learning=True, clock=None)；create_vlan(vlan_id, *, subnet, gateway_ip, gateway_mac, uplink_port="uplink")；remove_vlan(vlan_id)；bind_static(vlan_id, ip, mac, *, port)；bind_dynamic(vlan_id, ip, mac, *, port, now=None)；remove_binding(vlan_id, ip)；lookup(vlan_id, ip)；inspect(vlan_id, *, src_ip, src_mac, dst_ip, port, now=None)；quarantine_port(port, *, now=None, reason="manual")；release_port(port)；is_quarantined(port, *, now=None)；purge(now=None)；snapshot()；restore(state)；stats()；reset_stats()。校验失败抛 ArpGuardError(code)，code 用下面字符串。create_vlan 返回 vlan_id、subnet、gateway_ip、gateway_mac、uplink_port；绑定登记返回 kind 和 created，created 为真表示新建或替换了记录，完全重复登记为假；lookup 返回 mac、port、kind；inspect 返回 action、kind、vlan_id、ip、mac、port、dst_ip；quarantine_port 返回 port、quarantined、until。

故障清单与边界：一、建 VLAN 时网关必须落在 subnet 内，否则 ARP_SUBNET_MISMATCH；同 VLAN 重复注册，参数全同幂等返回，subnet 或网关不同抛 ARP_VLAN_CONFLICT；每个 VLAN 同时建立一条网关静态绑定，MAC 统一小写冒号格式。二、静态绑定不可被动态绑定覆盖（ARP_BINDING_CONFLICT），静态改不同 MAC 或端口同样冲突，完全相同的重复登记幂等；静态可以替换已过期的动态记录。三、动态绑定到期后允许换 MAC 或端口重新登记，到期前冲突；同一 IP/MAC/端口重复登记只刷新时间和过期点。四、inspect 遇到未登记源 IP：learning 为真时学习为动态绑定并返回 action=learned，否则抛 ARP_UNKNOWN_BINDING；命中绑定时 MAC 和端口都必须一致，否则算违规。五、网关 MAC 从非 uplink 端口出现、或网关 IP 被别的 MAC 冒领，都算违规；从 uplink 端口带网关 MAC 和网关 IP 的报文正常放行，kind 为 static。六、违规连续计数按端口算，任意一次正常放行清零；达到 violation_threshold 立即隔离该端口并抛 ARP_VIOLATION；被隔离端口后续检查抛 ARP_PORT_QUARANTINED，到 quarantine_seconds 后自动失效，release_port 手动解除并返回是否真的解除过。七、绑定总数不得超过 max_bindings，写满前先清掉已过期的动态绑定，仍满抛 ARP_TABLE_FULL 且不写入。八、lookup 对未知 VLAN 抛 ARP_UNKNOWN_VLAN，表里没有该 IP 返回 None；remove_binding 和 remove_vlan 返回布尔值，删 VLAN 连带删掉它的绑定，不影响其他 VLAN。九、purge 清到期动态绑定与到期隔离记录，返回清理条数。十、snapshot 返回 version、vlans、bindings、quarantine、stats 五个字段，version 为 1，bindings 以 "vlan:ip" 为键、值含 mac/port/kind/last_seen/expires_at，必须是深拷贝；restore 结构非法抛 ARP_SNAPSHOT_INVALID 且原状态不变。十一、stats 固定六个键 inspections、accepted、learned、violations、rejected、quarantined，inspections 等于 accepted+learned+violations+rejected，reset_stats 全部归零。

硬约束：inspect、绑定写入、隔离与快照全程持锁，8 个线程同时首包同一 IP 只能产生一条动态绑定，其余全部按 allow 计数；快照可跨进程恢复，恢复后继续检查时绑定、隔离和统计一致；任何路径下绑定表不得突破 max_bindings。时间参数 now 接受 ISO 8601 字符串，缺省用当前 UTC 时间。

验收：跑 `python3 -m pytest -q tests`，15 条必须全过；测试不可改、不可跳过、不可针对固定输入写死；并发与恢复自行验证；实现只落在本地。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 地狱
