# 联邦信任锚与实体声明链校验

## 题面

教育联盟上个月接入第四家高校 IdP，联合登录开始随机报无法确认签发方：A 校的锚点明明在信任列表里，B 校签发的实体声明却解析不出路径；一家被通报的机构撤销后，它下游的院系仍然能通过校验；还有一次中间节点把 authority_hints 指到别处，链照样被拼出来；最长链限制在五跳，实际却拼出七跳才报错。范围是 gsb/batch47-safety-a-10 下的 federation_trust_guard.py，类 FederationTrustGuard，负责信任锚、实体配置、下级声明、链解析、实体校验、撤销清理和统计，失败统一抛 FederationError(code, detail)，code 用下面这些字符串。

公开接口：FederationTrustGuard(*, max_chain_length: int = 5, clock=None)；add_trust_anchor(entity_id: str, *, jwks_fingerprint: str, expires_at: str, now=None) -> dict；add_entity_configuration(entity_id: str, *, issuer: str, jwks_fingerprint: str, authority_hints=None, metadata=None, expires_at: str, now=None) -> dict；add_subordinate_statement(issuer: str, *, subject: str, jwks_fingerprint: str, metadata=None, expires_at: str, now=None) -> dict；resolve_chain(entity_id: str, *, now=None) -> list；verify_entity(entity_id: str, *, expected_fingerprint: str, now=None) -> dict；trusted_entities(now=None) -> list；revoke_entity(entity_id: str, *, cascade: bool = True, reason: str = "manual") -> int；is_revoked(entity_id: str) -> bool；purge(now=None) -> int；snapshot() -> dict；restore(state: dict) -> None；stats() -> dict；reset_stats() -> None。

故障与边界：一、add_trust_anchor 要求 entity_id 与指纹非空，记录指纹和到期时间，重复登记同内容幂等，指纹或到期不同抛 FED_CONFLICT。二、add_entity_configuration 要求 issuer 等于 entity_id，authority_hints 传空集合抛 FED_INVALID_CONFIG，传 None 表示不限制，重复登记一致幂等，不一致抛 FED_CONFLICT。三、add_subordinate_statement 要求 issuer 已登记为锚点或实体配置，否则抛 FED_UNKNOWN_ENTITY；issuer 与 subject 相同、subject 为空、指纹为空抛 FED_INVALID_STATEMENT；同一 issuer 与 subject 重复登记一致幂等，不一致抛 FED_CONFLICT。四、resolve_chain 从任一有效锚点出发返回锚点到目标的实体 ID 列表，目标本身是锚点时返回单元素列表。五、同一目标存在多条路径时取边数最少，边数相同按完整路径字典序取最小，多次调用结果必须一致。六、未登记实体抛 FED_UNKNOWN_ENTITY，完全没有锚点抛 FED_NO_TRUST_ANCHOR，结构上无路径抛 FED_NO_TRUST_PATH。七、最短结构路径的边数超过 max_chain_length 抛 FED_CHAIN_TOO_LONG，不因为还存在更长的满足 hints 的路径而放行。八、路径上任一声明或锚点到期抛 FED_EXPIRED，目标实体配置到期同样抛 FED_EXPIRED，正好等于到期时刻即视为过期。九、subject 的 authority_hints 非空时，只有 hints 内的 issuer 才能作为其上级，仅存在违反 hints 的路径时抛 FED_AUTHORITY_MISMATCH。十、revoke_entity 在 cascade 为 true 时递归撤销该实体及其全部下游实体并返回新增撤销数，cascade 为 false 时只撤销该实体和与它直接相关的声明，下游实体不进入撤销名单但会失去路径。十一、已撤销目标 resolve 抛 FED_REVOKED，撤销未知实体抛 FED_UNKNOWN_ENTITY，重复撤销返回 0，is_revoked 只反映实体撤销名单。十二、purge 删除到期锚点、实体配置和下级声明并返回条数，snapshot 覆盖 config、anchors、configs、statements、revoked、stats 且为深拷贝，restore 后信任关系与撤销状态继续有效，非法快照抛 FED_SNAPSHOT_INVALID 且原状态不变。

硬约束：解析、校验、撤销与快照全程持锁，八个线程同时校验同一叶子实体必须得到完全相同的链与统计，不能出现只拼出半条链的中间态；快照必须深拷贝、跨进程可恢复，恢复前后的后续写入互不影响；链长、路径选择与 trusted_entities 的排序必须确定，重复调用不得抖动；过期记录由 purge 释放，不允许信任表无界增长。

验收：python3 -m pytest -q tests 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现，不涉及上游提交。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 困难
