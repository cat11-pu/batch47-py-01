# GSB 网络与通信安全任务

## 题面

边缘设备上个月连续被源地址伪造打穿：内网主机把源 IP 改成公网段，绕过了只看出方向的 ACL；双出口机房因为回程走另一条链路，严格反向路径检查把正常业务全丢了；运营商把一段地址宣告过来，黑洞路由已经配好，但入向检查仍按明细路由放行；运维临时加的白名单第二天还在生效，早已过期的前缀继续被信任；同一前缀两条等价路由，检查结果每次落到不同接口，监控曲线来回跳；路由表被灌到几十万条，进程内存吃满；设备重启后白名单和接口模式全部回到默认，业务再次中断。

实现集中在 gsb/batch47-safety-c-05 的 urpf_source_guard.py，类 UrpfGuard，维护前缀路由和接口模式，对入向报文做源地址反向路径校验，并支持临时白名单与快照恢复。公开接口：UrpfGuard(*, default_mode="strict", max_prefixes=10000, clock=None)；add_route(prefix, *, interface, metric=100, next_hop="connected")；remove_route(prefix, *, interface=None)；route_lookup(ip)；set_interface_mode(interface, *, mode, allow_asymmetric=False)；interface_state(interface)；allow_source(prefix, *, interface=None, expires_at=None)；remove_allow(prefix, *, interface=None)；check(interface, *, src_ip, dst_ip, now=None)；purge(now=None)；list_routes(limit=100)；snapshot()；restore(state)；stats()；reset_stats()。失败抛 UrpfError(code)。

故障、边界与判定规则：一、前缀按最长匹配选路，同前缀多接口时取 metric 最小者，metric 相同按接口名升序取第一个，route_lookup 返回 prefix、interface、metric、next_hop、null_route、candidates，candidates 为该前缀全部接口名排序结果。二、add_route 对同一前缀同一接口重复登记且参数相同幂等返回 created 为假，参数变化则更新并保持 created 为假；新前缀才占容量，超过 max_prefixes 抛 URPF_TABLE_FULL；前缀非法抛 URPF_INVALID_PREFIX，next_hop 只接受 connected、null0 或合法地址。三、remove_route 指定 interface 时只删该接口，删空后前缀消失，返回是否删到了记录。四、接口模式只有 strict、loose、off，非法抛 URPF_INVALID_MODE；未登记接口使用 default_mode；off 直接放行并返回 reason 为 disabled。五、strict 模式下来源的最优路由出接口必须等于入接口，否则 drop，reason 为 interface_mismatch；相等时 accept，reason 为 strict_rpf；接口设置 allow_asymmetric 后 strict 降级为放行任意有路由的来源，reason 为 asymmetric_allow。六、loose 模式下只要存在来源路由就 accept，reason 为 loose_rpf；完全无路由 drop，reason 为 no_route。七、null0 路由优先级最高，即使来源命中白名单也 drop，reason 为 null_route。八、白名单按前缀登记，可限定单个接口也可以全局，命中最长前缀且接口限定优先于全局；命中返回 accept，reason 为 allowlisted；expires_at 到期后 check 不再命中，purge 删除到期项并返回条数。九、check 返回 action、reason、mode、interface、src_ip、dst_ip、prefix、route_interface，无匹配路由时 prefix 与 route_interface 为 None；地址非法抛 URPF_INVALID_ADDRESS。十、snapshot 返回 version、routes、interfaces、allows、stats，版本为 1，必须是深拷贝；restore 结构非法抛 URPF_SNAPSHOT_INVALID 且原状态不变。十一、stats 固定五个键 checked、accepted、dropped、allow_hits、no_route，参数校验失败不计入 checked，reset_stats 全部归零。十二、list_routes 按前缀字符串排序并遵守 limit。

硬约束：路由增删、模式设置、白名单、check、清理与快照全程持锁，8 个线程同时对同一来源做 strict 检查，结果全部一致且 checked 与 accepted 精确各加 8；快照跨进程恢复后路由、接口模式、白名单与统计保持一致，恢复前后继续检查行为不变；任意时刻不同前缀数量不超过 max_prefixes，白名单过期项不得继续参与判定。now 接受 ISO 8601 字符串，缺省取当前 UTC 时间。

验收：`python3 -m pytest -q tests` 15 条全部通过；不得修改测试、跳过或按固定输入写死；并发和恢复场景自行验证；只做本地实现，不依赖外部服务。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 困难
