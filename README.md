# 任务说明

依赖混淆事故复盘会开了三个小时，结论却只有一句：内部包的 scoped 名称在公共镜像上被人抢先注册，构建机按名字取包时选中的是外部制品；同一周还有两次发版把已 yank 的版本重新装进镜像，锁文件里写死的 digest 与实际拉下来的对不上，审计台账也查不出是谁在什么时间放行的。当前解析只看谁先注册，优先级、信任标记、作用域归属和吊销状态都没有参与决策，同一个制品还能被反复记成多条放行记录；各小组维护的锁文件各说各话，pin 的 digest 与 registry 里实际发布的记录经常对不上。请修 gsb/batch47-safety-b-08 下的 package_provenance_admission.py，公开类型 ProvenanceAdmission，异常 ProvenanceError(code, detail)，只改这个文件，不要动 tests 目录。

保持这些公开接口：`ProvenanceAdmission(*, max_publications=10000, max_ledger=200)`；`register_registry(name, *, ecosystem, priority, trusted=True) -> dict`；`revoke_registry(name) -> None`；`claim_scope(ecosystem, scope, *, registry) -> dict`；`register_project(project, *, policy="pinned") -> dict`；`publish(ecosystem, name, version, *, registry, digest, yanked=False) -> dict`；`yank(ecosystem, name, version, *, registry) -> dict`；`pin(project, ecosystem, name, *, version, digest) -> dict`；`resolve(project, ecosystem, name, version, *, now=None) -> dict`；`admit(project, ecosystem, name, version, *, now=None) -> dict`；`ledger(project, *, limit=50) -> list`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。

故障与边界：一、同一生态内优先级不能重复，重复注册抛 PROV_PRIORITY_CONFLICT，注册名复用但配置不同抛 PROV_REGISTRY_CONFLICT，用错生态的 registry 发布抛 PROV_REGISTRY_ECOSYSTEM，未登记 registry 抛 PROV_UNKNOWN_REGISTRY；二、scope 只能由已登记 registry 认领，已被别家认领再认领抛 PROV_SCOPE_CONFLICT；三、带 scope 的包名只允许归属 registry 发布，别家发布抛 PROV_SCOPE_VIOLATION，未认领 scope 发布抛 PROV_UNCLAIMED_SCOPE；四、版本必须是三段数字，digest 必须是 sha256: 加 64 位小写十六进制，包名不合规抛 PROV_BAD_NAME，否则分别抛 PROV_BAD_VERSION、PROV_BAD_DIGEST；五、同一 registry 同一版本同 digest 重复发布幂等返回 exists，不同 digest 抛 PROV_DIGEST_CONFLICT；六、发布总量超过 max_publications 抛 PROV_CAPACITY 且不落地；七、yank 后该版本不再参与解析，只剩被 yank 的候选抛 PROV_YANKED，未知发布记录抛 PROV_UNKNOWN_PUBLICATION；八、无 scope 的名称在多个 registry 同时存在时选优先级最高且未被吊销、被标记可信的候选，全部候选被吊销抛 PROV_REGISTRY_REVOKED，只剩不可信 registry 抛 PROV_UNTRUSTED_REGISTRY；九、pinned 项目没有 pin 抛 PROV_UNPINNED，pin 版本不符抛 PROV_PIN_MISMATCH，pin digest 与选中制品不符抛 PROV_DIGEST_MISMATCH，permissive 项目无 pin 可以放行；十、pin 一旦写入不可改，同值重复写入返回 unchanged，不同值抛 PROV_PIN_CONFLICT；十一、admit 对同一 project 已放行过的同一 artifact 与 digest 返回 duplicate 且不重复写台账，新制品按 sequence 递增，ledger 按从新到旧返回，limit 限 1 到 200，否则抛 PROV_BAD_LIMIT，台账超过 max_ledger 时丢弃最旧记录；十二、resolve 返回 registry、digest、priority 与 scope，snapshot 覆盖 version、registries、scopes、projects、pins、publications、ledger、stats、sequence，必须深拷贝，restore 结构非法抛 PROV_SNAPSHOT_INVALID 且原状态不变。

硬约束：发布、解析、放行三条路径共用同一把锁，并发发布同一版本的多个 digest 只能有一份成功；同一制品被多线程同时放行只能产生一条台账记录；快照能跨进程恢复，恢复后 pin、吊销状态和台账去重仍然生效；发布总量与台账长度都有硬上限。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复要自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 地狱
