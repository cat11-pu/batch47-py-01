# GSB 网络与通信安全任务

## 题面

园区接入交换机换了批无线 AP 之后环路告警不断：AP 自带的小交换机把 BPDU 灌进接入口，根桥被抢走，整栋楼的流量绕行；接入端口收到 BPDU 却照常转发，日志里只有一行提示；上联口遇到更优根桥时端口状态没有收敛，来回震荡；有人把测试交换机接到根端口方向，根桥反复切换；拓扑变更报文每秒几十条，CPU 被打满；被 err-disable 的端口重启后又自动放行，第二天同样故障复现；运维手工恢复端口后没清计数，下一次误接立即又被锁死。

需要改的是 gsb/batch47-safety-c-04 里的 bpdu_guard.py，类型 BpduGuard，按端口角色检查入向 BPDU，执行 BPDU 防护与根防护，限制拓扑变更风暴并管理 err-disable。公开接口：BpduGuard(*, root_priority=None, tc_threshold=10, tc_window=10, err_disable_seconds=300, max_err_disable=2, clock=None)；register_port(port, *, role="access", bpdu_guard=None, root_guard=None)；unregister_port(port)；set_root_bridge(bridge_id, *, priority)；receive_bpdu(port, bpdu, *, now=None)；port_state(port, *, now=None)；is_err_disabled(port, *, now=None)；recover_port(port, *, now=None)；list_ports()；purge(now=None)；snapshot()；restore(state)；stats()；reset_stats()。失败抛 BpduError(code)。

故障现象与边界：一、role 只接受 access、uplink、root，非法抛 BPDU_INVALID_ROLE；access 默认 bpdu_guard 为真，uplink 和 root 为假，root 默认 root_guard 为真；重复注册参数全同幂等返回，角色或标志不同抛 BPDU_PORT_CONFLICT。二、bpdu 必须含 bridge_id、root_priority、bridge_priority、tc_flag，bridge_id 非空，优先级 0 到 65535 的整数，tc_flag 为布尔，缺项或类型错抛 BPDU_INVALID；未注册端口抛 BPDU_UNKNOWN_PORT。三、处于 err-disable 的端口再收到 BPDU 抛 BPDU_PORT_ERR_DISABLED。四、access 端口收到任何 BPDU 立即 err-disable，返回 action 为 err_disable、reason 为 bpdu_on_access。五、root 端口未配置根桥时收到 BPDU 抛 BPDU_ROOT_UNKNOWN；对端 root_priority 小于本机根优先级视为更优根，端口进入 root-inconsistent 并返回 action 为 root_inconsistent；收到不优于本机根的 BPDU 则放行并清除 root-inconsistent。六、非 BPDU 防护端口上，tc_flag 为真的 BPDU 按 tc_window 秒滑窗计数，窗口内数量超过 tc_threshold 立即 err-disable，返回 reason 为 tc_storm 和 tc_count；tc_events 记录该端口累计 TC 报文数。七、err-disable 持续 err_disable_seconds，purge 在到期后自动恢复并返回恢复条数；同一端口累计第 max_err_disable 次 err-disable 且到期后不再自动恢复，保持锁定直到 recover_port。八、recover_port 手动清除锁定并重置该端口的 err-disable 计数和 root-inconsistent 状态，返回是否处理过；set_root_bridge 的 priority 必须是 0 到 61440 且为 4096 的整数倍，否则抛 BPDU_INVALID_PRIORITY。九、receive_bpdu 返回 action，err_disable 时带 reason 和 tc_count；port_state 返回 port、role、err_disabled、stuck、root_inconsistent、tc_events、err_disable_count、last_seen、last_bridge_id；list_ports 按端口名排序，每行至少含 port 和 role；port_state 的 err_disabled 按传入时间实时计算，不要求先执行 purge。十、snapshot 返回 version、ports、root、stats，版本为 1，必须是深拷贝；restore 结构非法抛 BPDU_SNAPSHOT_INVALID 且原状态不变。十一、stats 固定五个键 received、accepted、err_disabled、root_inconsistent、purged，received 统计通过校验的 BPDU，accepted 只计正常放行，reset_stats 全部归零。

硬约束：receive_bpdu、端口注册、purge、恢复与快照全程持锁，8 个线程同时向同一上联口各发 5 个合法 BPDU，received 和 accepted 必须都精确等于 40，端口状态和 TC 计数不能错乱；快照跨进程恢复后端口角色、err-disable 剩余时间、根桥配置、TC 计数和统计保持一致；同一端口连续误接时 err-disable 计数只增不减，直到 purge 正常恢复或人工 recover。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得改动测试、跳过或用固定输入写死；并发与快照恢复自行验证；实现限定在本仓库本地。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 困难
