# batch47-safety-a-01

## 题面

内部授权网关从上周开始给各业务线签发 macaroon 凭证，让下游服务离线校验，不再每次回源。上线三天就收到一串故障单：过期时间被改早的凭证依旧放行；有人把 scope 从只读改成管理员，服务端照旧返回成功；同一张凭证在台账里被登记了两次，重启网关后已撤销的凭证重新能用；第三方授权中心没给 discharge 时下游也放行；绑定在某条 TLS 通道上的凭证被复制到另一条连接却没有告警；缓存里塞进几十万条标识后网关内存被打爆，进程重启后撤销名单整体丢失；两次并发校验同一张凭证时审计数字只加了一次；业务方还抱怨凭证只能整体签发，无法在不回源的前提下继续收窄权限。范围是 gsb/batch47-safety-a-01 下的 macaroon_caveat_guard.py，类 MacaroonCaveatGuard，负责根密钥登记与轮换、凭证签发、衰减、通道绑定、离线授权和标识台账。

公开接口：`MacaroonCaveatGuard(*, clock_skew: int = 60, max_caveats: int = 16, max_tokens: int = 500, clock=None)`；`rotate_root_key(key_id: str, secret: bytes) -> dict`；`revoke_root_key(key_id: str) -> None`；`mint(*, location: str, identifier: str, caveats=(), key_id=None, now=None) -> dict`；`attenuate(token: dict, caveat: dict) -> dict`；`authorize(token: dict, *, context=None, discharges=(), now=None) -> dict`；`bind(token: dict, *, binding: str) -> dict`；`inspect(token: dict) -> dict`；`revoke(identifier: str) -> None`；`is_revoked(identifier: str) -> bool`；`purge(now=None) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。所有拒绝统一抛 MacaroonError(code, detail)，code 取下面列出的字符串。authorize 成功返回 status 为 ok、identifier、key_id、caveat_count；inspect 返回 identifier、key_id、location、caveat_count、按原顺序排列的 kinds 以及 caveats 的深拷贝，改动返回值不得影响凭证本身。

故障与边界：一、凭证结构为 version/location/identifier/key_id/caveats/binding/signature，签名是对 identifier、绑定值和按顺序连接的 caveat 规范化串做 HMAC 链，改动任一 caveat 或直接替换 signature 抛 MACAROON_SIGNATURE_INVALID，缺字段抛 MACAROON_INVALID_TOKEN；二、同一 identifier 重复签发抛 MACAROON_DUPLICATE_IDENTIFIER，且不增加 minted；三、scope caveat 逐条求交，context 的 scopes 缺任一取值抛 MACAROON_SCOPE_MISSING，context caveat 键值不等抛 MACAROON_CONTEXT_MISMATCH；四、expires caveat 允许 clock_skew 秒宽限，now 恰好等于到期时间加宽限即抛 MACAROON_EXPIRED；五、衰减只能追加一条 caveat，返回新凭证且原凭证与其 caveats 不变，超过 max_caveats 抛 MACAROON_TOO_MANY_CAVEATS，kind 非法抛 MACAROON_CAVEAT_INVALID；六、third_party caveat 必须有 identifier 等于 caveat_key 的 discharge，没有则抛 MACAROON_DISCHARGE_REQUIRED；discharge 的 binding 必须等于父凭证 signature，该绑定只用于关联父凭证、不按通道绑定校验，不符或自身 caveat 不满足抛 MACAROON_DISCHARGE_INVALID；七、bind 返回绑定后的新凭证、原凭证保持不变，同一凭证只允许绑定一次，重复绑定抛 MACAROON_ALREADY_BOUND，授权时 context 的 binding 与凭证不符抛 MACAROON_BINDING_MISMATCH；八、未登记密钥抛 MACAROON_UNKNOWN_KEY，已撤销密钥抛 MACAROON_KEY_REVOKED，同 key_id 换 secret 抛 MACAROON_KEY_CONFLICT，同 secret 重复登记幂等，签发时省略 key_id 使用最近登记且未撤销的密钥；九、revoke 按 identifier 生效且可重复调用，命中抛 MACAROON_REVOKED；十、台账上限 max_tokens，写满先清理已到期条目，仍满抛 MACAROON_LEDGER_FULL 且不落账；十一、purge 删除到期条目并返回条数，到期判定同样含 clock_skew；十二、snapshot 覆盖密钥、台账、撤销名单和统计，必须是深拷贝，restore 后密钥仍可验签、撤销仍然生效，结构非法抛 MACAROON_SNAPSHOT_INVALID 且原状态不变。

硬约束：mint、authorize、revoke、rotate_root_key 全程持锁，十路并发对同一批标识签发与授权时 minted 与 authorized 必须与调用次数完全相等，不能出现丢计数或重复落账；快照跨进程恢复后不得与恢复前的写入共享可变结构；台账按到期时间淘汰，禁止无界增长；stats 固定返回 minted、authorized、denied、attenuated 四个整数，reset_stats 归零。

验收：`python3 -m pytest -q tests` 的 15 条测试必须全部通过；不得修改测试、跳过或按固定输入写死；并发和重启恢复场景自行验证；只做本地实现，不涉及上游提交。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 地狱
- module: macaroon_caveat_guard
- typeName: MacaroonCaveatGuard
