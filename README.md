# Sysctl 加固基线与命名空间取值

## 题面

主机加固平台把 sysctl 基线从脚本改成 profile 以后，线上出现一批半套配置。一次 apply 里三个键，前两个写成功、第三个值非法，结果机器停在中间状态，重启后没人知道基线到底生效了几条；两个 profile 同时声明 vm.swappiness，一个 10 一个 20，平台按加载顺序后者覆盖前者，没有冲突提示；手工 set 过的键在下次 apply 时被悄悄改回，值班同学以为变更没生效；容器网络命名空间与宿主机共用同一份键表，改容器里的 ip_forward 把宿主机也带走了；profile 注册时不校验类型，把 bool 键写成 yes 也能通过，运行时才报错；int 键写了 999999 超出内核范围，仍然落库；history 没有上限，一天涨到几十万条；回滚到某个 generation 时发现旧值早被覆盖，rollback 直接清空所有键；重启后 schema、profile 和 generation 全部丢失；两个变更窗口并发 apply 不同 profile，后写的把对方的键覆盖；drift 只比较已设置的键，未设置的键永远显示一致。相关代码在 gsb/batch47-safety-e-05 的 sysctl_profile_guard.py，类 SysctlProfileGuard，只用 Python 标准库，失败统一抛 SysctlError(code, detail)。

接口保持：`__init__(self, *, max_profiles: int = 64, max_keys_per_apply: int = 256, history_limit: int = 100)`；`register_schema(self, key: str, *, kind: str, minimum=None, maximum=None, allowed=None, default: str | None = None) -> dict`；`register_profile(self, name: str, settings: dict) -> dict`；`remove_profile(self, name: str) -> bool`；`apply(self, names, *, namespace: str = "host") -> dict`；`get(self, key: str, *, namespace: str = "host") -> str | None`；`set(self, key: str, value: str, *, namespace: str = "host") -> dict`；`unset(self, key: str, *, namespace: str = "host") -> bool`；`drift(self, name: str, *, namespace: str = "host") -> list`；`rollback(self, generation: int) -> dict`；`history(self, limit: int | None = None) -> list`；`list_profiles(self) -> list`；`stats(self) -> dict`；`snapshot(self) -> dict`；`restore(self, state: dict) -> None`。

故障与边界：一、键名必须是小写点分路径，至少两段，非法抛 SYSCTL_BAD_KEY；命名空间名只接受小写字母数字与下划线、冒号、短横，超过 32 位或含空格抛 SYSCTL_BAD_NAMESPACE；二、schema 的 kind 只允许 bool、int、string、enum，非法抛 SYSCTL_BAD_KIND；int 必须给出最小值和最大值且前者不大于后者，否则抛 SYSCTL_BAD_RANGE；enum 必须有非空候选集，否则抛 SYSCTL_BAD_SCHEMA；默认值不合法抛 SYSCTL_BAD_VALUE；重复注册抛 SYSCTL_SCHEMA_EXISTS；三、bool 只接受 0 与 1，int 必须是十进制整数并落在闭区间内，enum 只接受候选值，其余抛 SYSCTL_BAD_VALUE；四、profile 名非法抛 SYSCTL_BAD_PROFILE，重复抛 SYSCTL_PROFILE_EXISTS，settings 不是非空字典抛 SYSCTL_BAD_ARGUMENT，未知键抛 SYSCTL_UNKNOWN_KEY，单 profile 键数或注册总数超限分别抛 SYSCTL_BATCH_TOO_LARGE 与 SYSCTL_PROFILE_LIMIT；五、apply 接受单个名字或名字列表，空列表抛 SYSCTL_EMPTY_APPLY，类型不对或混入非字符串抛 SYSCTL_BAD_ARGUMENT，未知 profile 抛 SYSCTL_UNKNOWN_PROFILE，一次涉及的不同键数超过 max_keys_per_apply 抛 SYSCTL_BATCH_TOO_LARGE；六、同一命名空间内，新 profile 与已生效 profile 对同一键给出不同值时抛 SYSCTL_CONFLICT，detail 为冲突键，冲突时任何值都不落库；相同值允许并存；七、apply 成功返回 namespace、generation、applied、changed 四个字段，changed 为实际变化的键数，只有 changed 大于零才递增 generation 并写 history；八、set 返回 key、value、changed、generation，值未变化不递增 generation；unset 删除覆盖值并回落到 schema 默认值，键本来没有覆盖值时返回 False；九、drift 按 profile 期望值逐键比较，未设置的键用 schema 默认值参与比较，返回按字典序排序的差异键列表；十、history 从新到旧返回，最多保留 history_limit 条，limit 为负抛 SYSCTL_BAD_LIMIT，limit 为 0 返回空列表；十一、rollback 只接受仍保留快照的 generation，否则抛 SYSCTL_UNKNOWN_GENERATION，成功时恢复该代全部命名空间的取值并生成新一代；十二、snapshot/restore 覆盖 schema、profile、取值、已生效集合、generation、历史快照、history 与统计，结构非法抛 SYSCTL_SNAPSHOT_INVALID 且原状态不变。

硬约束：apply 与 set 在同一把锁内完成冲突检查、取值写入和 generation 递增，多个线程并发 apply 不同 profile 不得互相覆盖或丢键；一次 apply 要么全部键生效要么一个都不改；历史与 generation 快照都有上限，不得无界增长；统计精确给出 applies、sets、rollbacks、conflicts、validation_errors。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- difficulty: 困难
