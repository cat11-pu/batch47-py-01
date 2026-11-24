# GSB 任务

## 题面

mTLS 网关给十二家合作方 CA 签发的客户端证书放行，本季度开始按信任锚上的名称约束做二次校验。安全评审复现了几个绕过：证书 SAN 写 evil-example.com 却被 example.com 的后缀规则放行；约束写成 .example.com 时，example.com 这个裸域仍被接受；excluded 命中之后 permitted 仍然给出允许；带通配符的证书直接通过；只配了 IPv4 网段的锚点放过了 IPv6 地址；没有 SAN 的证书被当成无约束处理；同一张证书连续校验两次，第二次把第一次的拒绝覆盖成了允许；证书链深度超过锚点上限也没有任何提示。

范围是 gsb/batch47-safety-b-03 下的 name_constraints_guard.py，类 NameConstraintsGuard，负责信任锚登记、SAN 集合评估、决策缓存与吊销。公开接口：NameConstraintsGuard(*, max_anchors: int = 32, decision_cache: int = 256, max_san: int = 32)；register_anchor(anchor_id: str, *, permitted=None, excluded=None, max_chain_depth: int = 5, allow_empty_san: bool = False) -> dict；revoke_anchor(anchor_id: str) -> None；evaluate(anchor_id: str, *, fingerprint: str, san: dict, chain_depth: int) -> dict；list_anchors() -> list；anchor(anchor_id: str) -> dict；is_revoked(anchor_id: str) -> bool；snapshot() -> dict；restore(state: dict) -> None；stats() -> dict；reset_stats() -> None。permitted 与 excluded 都是字典，键只允许 dns、email、ip，值为字符串列表。登记或吊销失败抛 ConstraintError(code, detail)；评估结果返回 {"allowed", "anchor_id", "fingerprint", "code", "violations", "cached"}，策略拒绝走返回值的 allowed=False 与 code，不抛异常。

故障与边界：一、重复登记同名锚点且配置一致返回 created=False，配置不同抛 CONSTRAINT_CONFLICT，锚点数量超过 max_anchors 抛 CONSTRAINT_CAPACITY_FULL；二、未知类型键、空字符串、非法 CIDR、带星号的 DNS 约束抛 CONSTRAINT_INVALID，链深上限小于 1 同样拒绝；三、DNS 约束 example.com 命中裸域和任意层级子域，前导点约束 .example.com 只命中子域，evil-example.com 与 example.com.evil 都不能命中；四、excluded 优先于 permitted，命中时返回 NAME_EXCLUDED 并带上命中的约束原文；五、同一类型配置了 permitted 却一条都不命中返回 NAME_NOT_PERMITTED，没有配置 permitted 的类型除 excluded 外默认放行；六、通配符 SAN 返回 NAME_WILDCARD_DENIED，未知 SAN 类型返回 NAME_SAN_TYPE_UNKNOWN，空值、非法邮箱、非法 IP 返回 NAME_SAN_INVALID；七、空 SAN 或全部为空列表时，只有锚点 allow_empty_san=True 才放行，否则 NAME_SAN_EMPTY，SAN 条目总数超过 max_san 返回 NAME_SAN_TOO_MANY；八、email 约束支持裸域、前导点子域和完整邮箱三种写法，完整邮箱只匹配同一地址；九、IP 约束按 CIDR 判断且 IPv4 与 IPv6 不互相匹配，excluded 网段同样优先；十、chain_depth 超过锚点上限返回 NAME_DEPTH_EXCEEDED，同一次评估的所有违规都要进入 violations，code 取第一条；十一、指纹必须是 64 位十六进制，未知或已吊销锚点分别抛 CONSTRAINT_UNKNOWN_ANCHOR 与 CONSTRAINT_ANCHOR_REVOKED，anchor 与 is_revoked 对未知锚点同样抛 CONSTRAINT_UNKNOWN_ANCHOR，is_revoked 对已吊销锚点返回 True；十二、决策缓存按锚点、指纹、链深和 SAN 规范化内容做键，容量超过 decision_cache 按最久未用淘汰，snapshot 深拷贝锚点、缓存与统计，restore 结构非法抛 CONSTRAINT_SNAPSHOT_INVALID 且状态不变。

硬约束：evaluate 全程持锁，多个线程同时校验同一张证书只做一次完整判定，其余命中缓存并把 cached 置为 True；吊销锚点必须立即清掉该锚点的缓存决策，不能出现吊销后仍命中旧允许；锚点表、缓存和单次 SAN 数量都有硬上限；stats 精确区分 evaluated、cached、allowed、denied，并发下不丢计数。

验收：python3 -m pytest -q tests 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发校验与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 困难
- module: name_constraints_guard
- typeName: NameConstraintsGuard
