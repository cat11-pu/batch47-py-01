# 题面

企业通信平台上周升级了边界会话控制器，SIP 分机的注册投诉一下子堆了四十多单。一线反馈：分机重连时会话定时器疯狂刷新，数据库里同一个联系地址被写了十几条；从别处抓来的 REGISTER 包原样重放一次，注册中心照样返回 200，等于攻击者可以一直把话务劫持到自己的地址；有的客户终端的 CSeq 偶尔倒退，我们没拦住，后注册的请求反而覆盖了先注册的绑定；expires 传成 0 的注销请求被当成普通注册，分机掉线后仍能呼通；某个租户的软交换被入侵后批量注册了上百个 Contact，把分机表撑爆；凌晨滚动升级后，注册中心重启，nonce 计数和绑定全部归零，刚抓到的旧包又能注册成功。运营要求把注册校验收敛到一个可观测、可恢复、能扛并发的组件里。

范围是 gsb/batch47-safety-d-07 下的 sip_registration_guard.py，类 SipRegistrationGuard。公开接口（Python 类型注解）：`SipRegistrationGuard(*, min_expires: int = 60, max_expires: int = 3600, default_expires: int = 300, max_contacts: int = 8, nonce_ttl: int = 300, clock=None)`；`add_realm(realm: str, *, algorithm: str = "MD5", qop: str = "auth") -> dict`；`add_user(aor: str, *, password: str) -> dict`；`issue_nonce(realm: str, *, now=None) -> dict`；`submit_register(request: dict, *, now=None) -> dict`；`query_bindings(aor: str) -> list`；`expire_bindings(*, now=None) -> int`；`remove_binding(aor: str, *, contact: str) -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 SipError(code, detail)，code 取下面列出的字符串。

故障与边界：一、realm 重复登记且配置相同幂等返回 created 为 False，配置不同抛 SIP_REALM_CONFLICT；新 realm 只接受 MD5 与 qop=auth，否则抛 SIP_UNSUPPORTED_ALGORITHM 或 SIP_UNSUPPORTED_QOP；二、AOR 所属 realm 未登记抛 SIP_UNKNOWN_REALM，密码为空抛 SIP_BAD_PASSWORD，同 AOR 重复登记口令不同抛 SIP_CREDENTIAL_CONFLICT；三、REGISTER 缺 Authorization 抛 SIP_AUTH_REQUIRED，摘要字段缺失或 nc 不是八位十六进制抛 SIP_BAD_AUTH_DIGEST，realm 与用户不符抛 SIP_REALM_MISMATCH，unknown nonce 抛 SIP_UNKNOWN_NONCE，nonce 属于别的 realm 抛 SIP_NONCE_REALM_MISMATCH，超过 nonce_ttl 抛 SIP_STALE_NONCE，摘要 response 不符抛 SIP_AUTH_FAILED；摘要按 MD5(HA1:nonce:nc:cnonce:qop:HA2) 计算，HA1 为 MD5(username:realm:password)，HA2 为 MD5(REGISTER:uri)；四、同一 AOR 与 nonce 的 nc 必须严格递增，重复或回退抛 SIP_REPLAY，且失败的摘要不消耗 nc；五、CSeq 必须严格递增，同一 Call-ID 与 CSeq 且 Contact 相同视为重传，幂等返回原结果并标记 retransmission，若 Contact 变了抛 SIP_CSEQ_REGRESSION，CSeq 非正整数抛 SIP_BAD_CSEQ；六、expires 缺省取 default_expires，低于 min_expires 抬到下限，高于 max_expires 压到上限，Contact 参数里的 expires 只在请求未给时生效，负数或非数字抛 SIP_BAD_EXPIRES；七、expires 为 0 只注销指定 Contact，返回 registered 为 False，未绑定过也算成功但 removed 为 False；八、每个 AOR 的 Contact 数超过 max_contacts 抛 SIP_CONTACT_QUOTA，更新已有 Contact 不占新配额；contact 必须是 sip 或 sips URI、不含空白与控制字符且带主机部分，否则抛 SIP_BAD_CONTACT；九、query_bindings 按 contact 升序返回，expire_bindings 只清理到期绑定并返回条数；remove_binding 对不存在的 Contact 抛 SIP_NO_SUCH_BINDING；十、stats 固定七项：registered、deregistered、auth_failed、replayed、expired、rejected、retransmitted，reset_stats 只清零计数；十一、snapshot 必须带上 realm、用户口令校验材料、nonce 及其到期时间、nc 计数、绑定、CSeq 状态和统计，restore 后旧 nonce 的重放仍被拦住、绑定不丢，结构非法抛 SIP_SNAPSHOT_INVALID 且原状态不变。

硬约束：submit_register 全程持锁，八个线程用各自 nonce 同时重传同一 Call-ID 与 CSeq 时只能产生一条绑定，其余全部按重传返回；nc 计数与 CSeq 判断必须是原子的，不能出现两条并发请求都判为新注册；快照必须深拷贝并能跨进程恢复，恢复后不能把已经用过的 nonce 计数当成未用。

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
