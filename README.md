# 802.1X 端口接入守卫

## 题面

园区网改造验收刚过，接入层的投诉就堆了起来：访客把笔记本插进会议室端口，不做任何认证就拿到了业务网地址；被并购的分支把一台小交换机的旁路配置带了过来，同一个账号在两台接入设备上同时在线，计费单对不上；上周还有人对着一个端口连续试了几十次口令，端口照旧放行重试，认证服务器差点被打挂。认证日志里看不到失败次数，也说不清端口当前挂在哪个 VLAN，值班只能重启端口碰运气；换端口重连的账号还会把新会话叠在旧会话上，计费侧因此多出了重复话单。安全组要求把认证器的端口状态机、会话和 VLAN 分配收拢到一个独立模块里，先把这些行为固定下来。范围是 gsb/batch47-safety-c-07 下的 dot1x_port_guard.py，类 Dot1xPortGuard。

公开接口：`Dot1xPortGuard(*, reauth_interval: int = 3600, max_failures: int = 3, hold_down: int = 300, max_sessions_per_port: int = 4, max_ports: int = 48, clock=None)`；`add_port(port, *, mode="auto", access_vlan=100, guest_vlan=None, max_sessions=None) -> dict`；`set_port_mode(port, mode) -> dict`；`set_session_limit(port, limit) -> dict`；`eapol_start(port, *, now=None) -> dict`；`authenticate(port, *, identity, method, credential_ok, vlan=None, now=None) -> dict`；`deauthenticate(port, *, identity=None, now=None) -> int`；`port_state(port, *, now=None) -> dict`；`sessions(port) -> list`；`failure_count(port) -> int`；`tick(now=None) -> dict`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败抛 Dot1xError(code, detail)，code 用下面列出的字符串。

故障与边界：一、未登记端口统一抛 DOT1X_UNKNOWN_PORT；同端口重复登记且可见配置一致要幂等返回，任一项不同抛 DOT1X_CONFLICT；mode 只认 auto、force-authorized、force-unauthorized，否则 DOT1X_INVALID_MODE；两个 VLAN 都必须在 1 到 4094 之间，否则 DOT1X_INVALID_VLAN；端口数超过 max_ports 抛 DOT1X_PORT_LIMIT，注册失败不占位。二、auto 端口初始 unauthorized，配了 guest_vlan 就落在访客 VLAN；force-authorized 初始即 authorized 并使用 access_vlan；force-unauthorized 保持在 unauthorized。三、eapol_start 让 auto 端口进入 authenticating 并返回端口、状态、当前 VLAN；对 force-unauthorized 端口调用抛 DOT1X_PORT_FORCED_UNAUTHORIZED。四、authenticate 成功后建立会话，返回 authorized、identity、vlan 和 reauth_at，reauth_at 等于 now 加 reauth_interval；传了 vlan 用动态 VLAN，否则用 access_vlan；sessions 按 identity 升序。五、凭证错误返回 rejected 和 DOT1X_AUTH_FAILED，failures 累加；达到 max_failures 后端口进入 held，held_until 等于 now 加 hold_down，并清掉该端口全部会话；held 期间再认证抛 DOT1X_PORT_HELD。六、tick 结算两件事：held_until 到期释放端口并把 failures 清零；reauth_at 到期的会话撤销并计数，端口没有会话就回到 unauthorized。七、认证方法只接受 eap-md5、peap、tls，其余抛 DOT1X_METHOD_UNSUPPORTED；identity 去掉空白后为空抛 DOT1X_INVALID_IDENTITY。八、同一个 identity 全局只能有一个会话，换到第二个端口抛 DOT1X_IDENTITY_IN_USE，先 deauthenticate 才能迁移。九、端口会话上限以端口策略优先，否则用全局 max_sessions_per_port，超限抛 DOT1X_SESSION_LIMIT。十、deauthenticate 指定 identity 不存在抛 DOT1X_SESSION_NOT_FOUND，不指定则清空整端口并返回删除条数；force-authorized 端口不接受 authenticate，抛 DOT1X_MODE_CONFLICT。十一、snapshot 必须深拷贝端口配置、会话、失败计数、held 期限和统计；restore 遇到结构非法抛 DOT1X_SNAPSHOT_INVALID，且现有状态一条不动，恢复后 held 端口仍然拦住认证。

硬约束：authenticate、deauthenticate、tick 互斥，八个线程同时认证同一个 identity 只能有一个 authorized，其余全部 DOT1X_IDENTITY_IN_USE；快照能跨进程恢复且与运行状态彻底分离；端口数、每端口会话数都必须有硬上限；stats 精确区分 authorized、denied、deauthorized、reauth_revoked，reset_stats 归零。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 地狱
