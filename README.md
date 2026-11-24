# 需求说明

## 题面

上周内核团队做驱动升级，一台承载支付流量的机器在重启后自动加载了一个已被拉黑的第三方模块，源头是加载脚本只比对了模块名，没有核对签名证书指纹；同一天另一台机器在卸载底层模块时把依赖它的四个模块一起拽崩，内核直接 panic。证书轮换之后，旧指纹签名的模块还在被放行，而撤销名单里的签发方换个名字又签了一个新模块进来。审计同事还发现污点标记在重启后全部丢失，谁也说不清那台机器到底加载过哪些带污点的模块。更麻烦的是，白名单文件里还留着上一任管理员手写的通配条目，模块名一改就匹配不上，加载脚本于是走了跳过校验的分支。这次要重做的是 gsb/batch47-safety-e-09 下的 module_load_guard.py，类 ModuleLoadGuard，负责模块白名单、签发方信任、签名校验、依赖顺序、卸载保护以及污点标记。公开接口：`ModuleLoadGuard(*, allow_unsigned: bool = False, max_loaded: int = 64)`；`trust_signer(signer: str, *, fingerprints, priority: int = 0) -> dict`；`revoke_signer(signer: str) -> None`；`allow_module(name: str, *, signer: str | None = None, fingerprint: str | None = None, depends=(), taints=()) -> dict`；`deny_module(name: str, *, reason: str = "") -> dict`；`load(name: str, *, signature: dict | None = None) -> dict`；`unload(name: str, *, force: bool = False) -> dict`；`is_loaded(name: str) -> bool`；`loaded() -> list`；`taints() -> list`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 ModuleError(code, detail)。故障与边界：一、签发方名称或指纹集合为空抛 MOD_INVALID_SIGNER，同一签发方重复登记不同指纹抛 MOD_CONFLICT，相同参数幂等；二、撤销未登记签发方抛 MOD_UNKNOWN_SIGNER，已撤销签发方再登记或签字一律抛 MOD_SIGNER_REVOKED；三、allow_unsigned 为假时登记无签发方的模块抛 MOD_SIGNATURE_MISSING；四、登记时签发方不存在抛 MOD_SIGNER_UNTRUSTED，指纹不在该签发方集合里抛 MOD_SIGNATURE_INVALID；五、同名模块参数不同抛 MOD_CONFLICT，参数完全相同幂等返回；六、依赖图出现环抛 MOD_DEPENDENCY_CYCLE，环上的模块不得留在白名单里；七、被 deny 的模块 load 时抛 MOD_DENIED，未登记模块抛 MOD_UNKNOWN；八、load 时缺签名或签名里的签发方、指纹与登记值不一致，分别抛 MOD_SIGNATURE_MISSING、MOD_SIGNATURE_INVALID，签发方在登记后被撤销抛 MOD_SIGNER_REVOKED；九、依赖未加载时抛 MOD_DEPENDENCY_MISSING，detail 按名字排序列出全部缺失项；十、已加载模块重复 load 返回 already_loaded 且不重复计数，加载数量达到 max_loaded 时新模块抛 MOD_LIMIT；十一、卸载有存活依赖的模块抛 MOD_IN_USE，force 为真时按依赖倒序级联卸载并在返回值里列出级联名单，卸载不存在的模块抛 MOD_NOT_LOADED，污点标记只增不减；十二、snapshot 深拷贝白名单、签发方、撤销名单、加载开关与数量上限、加载顺序与统计，restore 结构非法抛 MOD_SNAPSHOT_INVALID 且原状态不变，stats 分开记 loaded、unloaded、denied、rejected。硬约束：load 与 unload 全程持同一把锁，多线程并发加载同一模块只允许一个真正加载，其余返回 already_loaded；依赖顺序在恢复快照后仍必须成立；白名单、撤销名单、污点集合在快照往返中不得丢失或串号；模块的准入结论必须只依赖登记信息与签名，不能因为节点上已有同名文件而放行。验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发加载和重启恢复自行验证；只做本地实现，不接触真实内核模块。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- difficulty: 困难
