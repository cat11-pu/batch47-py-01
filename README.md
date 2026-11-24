# 令牌交换的委托链与子令牌治理

## 题面

合作方这周报了一起越权事故：一个只读的报表令牌经过两次转发后拿到了账单域的管理权限；安全团队回放请求时还发现，同一份委托在三秒内换出了十几张令牌，父令牌被撤销后下游令牌仍然能访问接口；另一个外包服务下线后，用它身份发起的交换照样成功。范围是 gsb/batch47-safety-a-09 下的 token_exchange_delegation_guard.py，类 TokenExchangeGuard，负责受众与客户端登记、根令牌签发、委托交换、校验、级联撤销和统计，失败统一抛 TokenExchangeError(code, detail)，code 用下面这些字符串。

公开接口：TokenExchangeGuard(*, max_delegation_depth: int = 3, default_ttl: int = 300, max_children_per_token: int = 8, dedupe_window: int = 0, clock=None)；register_client(client_id: str, *, allowed_audiences) -> dict；register_audience(audience: str, *, max_delegation_depth: int | None = None, allowed_scopes=None) -> dict；issue_root(subject: str, *, scopes, audience: str, ttl: int | None = None, now=None) -> dict；exchange(subject_token: str, *, client_id: str, audience: str, scopes=None, actor_token=None, now=None) -> dict；validate(token: str, *, audience: str, required_scope: str | None = None, now=None) -> dict；introspect(token: str, *, now=None) -> dict；chain(token: str) -> list；revoke(token: str, *, reason: str = "manual") -> int；revoke_principal(principal: str, *, reason: str = "offboarded") -> int；purge(now=None) -> int；snapshot() -> dict；restore(state: dict) -> None；stats() -> dict；reset_stats() -> None。

故障与边界：一、register_audience 与 register_client 校验名称和非空集合，重复登记同内容幂等，客户端配置不同抛 TX_CLIENT_CONFLICT，受众配置不同抛 TX_AUDIENCE_CONFLICT；交换时未知客户端抛 TX_UNKNOWN_CLIENT，未知受众抛 TX_UNKNOWN_AUDIENCE，客户端未获准该受众抛 TX_AUDIENCE_NOT_ALLOWED。二、issue_root 校验 subject 与 scopes 非空，scope 不得超出受众允许集，返回 generation 为 0、actor_chain 为空、expires_at 等于 now 加 ttl。三、exchange 的 scopes 必须是被交换令牌 scopes 的子集，空集合或越权抛 TX_SCOPE_ESCALATION，缩权后的令牌不能再放宽。四、受众登记 allowed_scopes 时，交换请求的 scopes 还必须落在该集合内，否则抛 TX_SCOPE_NOT_ALLOWED。五、actor_token 必须存在、未过期、未撤销，否则抛 TX_ACTOR_INVALID；actor 的 subject 与被交换令牌 subject 相同抛 TX_ACTOR_SELF。六、每次交换 generation 加一，超过全局 max_delegation_depth 抛 TX_DEPTH_EXCEEDED，受众单独设置的上限只能收紧不能放宽。七、子令牌 expires_at 取 now 加 default_ttl 与父令牌 expires_at 的较小值，任何子令牌不得比父令牌活得久。八、dedupe_window 大于 0 时，同一父令牌、同一客户端、同一受众、同一 scopes、同一 actor 在窗口内的重复交换返回已有令牌并标 deduped 为 true，不新建令牌。九、每个父令牌的子令牌数达到 max_children_per_token 后继续产生新交换抛 TX_FANOUT_EXCEEDED，命中已有令牌的重复交换不受该上限影响。十、validate 依次校验存在、撤销、到期、受众和必需 scope，分别抛 TX_UNKNOWN_TOKEN、TX_TOKEN_REVOKED、TX_TOKEN_EXPIRED、TX_AUDIENCE_MISMATCH、TX_SCOPE_MISSING。十一、revoke 递归撤销全部后代并返回新增撤销数，revoke_principal 撤销 subject 或 actor_chain 命中该主体的一切令牌及其后代，已撤销的返回 0。十二、snapshot 覆盖 config、clients、audiences、tokens、stats 且为深拷贝，restore 后父子关系、撤销状态与统计继续有效，非法快照抛 TX_SNAPSHOT_INVALID 且原状态不变。

硬约束：exchange、revoke 与快照全程持锁，八个线程用同一父令牌、同一参数并发交换时只能新建一张令牌，其余全部返回同一张令牌并标 deduped，统计必须是一张新建、七次去重；父子关系与 generation 必须原子更新，不能出现孤儿令牌；快照跨进程可恢复、深拷贝；委托深度、子令牌数量和 dedupe 窗口共同约束令牌膨胀，超过上限不得悄悄放行。

验收：python3 -m pytest -q tests 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现，不涉及上游提交。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 地狱
