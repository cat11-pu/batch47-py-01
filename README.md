# 任务说明

证书吊销数据在三个 CA 站点之间同步了两周，PKI 值守收到的工单越来越集中：一张早已吊销的中间证书仍在边缘网关被当作有效，独立机房里的同一份列表却又提前判成过期。查下来是基础 CRL 与增量 CRL 各存各的，增量里的 removeFromCRL 没有回滚基础条目，编号落后的文件被后到的覆盖，跨时区节点把 nextUpdate 当成本地时间；增量只增不减，长期不换 base 的目录服务内存一路上涨。值班同学每周还要手工合并一次列表，合并脚本会把已恢复的证书重新标回吊销，跨站点时钟漂移又让同一个序列号在两台机器上得到相反结论。请修 gsb/batch47-safety-b-06 下的 crl_delta_merger.py，公开类型 CrlDeltaMerger，异常 CrlError(code, detail)，只改这个文件，不要动 tests 目录。

保持这些公开接口：`CrlDeltaMerger(*, max_serials=5000, clock_skew=300, max_deltas=8, clock=None)`；`register_issuer(issuer, *, key_id, indirect=False) -> dict`；`list_issuers() -> list`；`submit_base(issuer, *, number, this_update, next_update, entries, signature_key) -> dict`；`submit_delta(issuer, *, base_number, number, this_update, next_update, entries, signature_key) -> dict`；`is_revoked(issuer, serial, *, now) -> dict`；`effective_crl(issuer, *, now) -> dict`；`status(issuer, *, now) -> dict`；`expire(*, now) -> int`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。

故障与边界：一、签发方重复登记且 key_id、indirect 完全一致时幂等返回，key_id 变化或 indirect 变化抛 CRL_CONFLICT，未登记签发方提交列表抛 CRL_UNKNOWN_ISSUER；二、base 编号必须是正整数且严格大于上一份，否则抛 CRL_STALE_NUMBER 且状态不变；三、签名 key 必须等于该签发方登记的 key_id，否则抛 CRL_BAD_SIGNATURE；next_update 必须晚于 this_update，否则抛 CRL_BAD_WINDOW；四、条目 serial 非空，reason 限 key_compromise、superseded、remove_from_crl 等固定取值，除 remove_from_crl 外 revoked_at 必须是可解析的时间串，否则抛 CRL_BAD_ENTRY，整份列表不落地；五、同一份 CRL 内重复 serial 以后出现的条目为准；六、delta 的 base_number 必须等于当前 base 编号，否则抛 CRL_BASE_MISMATCH，编号必须大于上一份 delta，否则抛 CRL_STALE_NUMBER，delta 的 next_update 不得超过 base 的 next_update；七、remove_from_crl 从合并结果里删除该序列号，后续 delta 可以再次加入；八、新旧 base 交替时旧 delta 全部作废；九、合并后的吊销条目超过 max_serials 抛 CRL_CAPACITY，单个 base 已有 max_deltas 份 delta 后继续提交抛 CRL_DELTA_LIMIT，两种情况都不改变已有状态；十、查询时间达到 next_update 加 clock_skew 即视为过期，抛 CRL_EXPIRED，expire 删除过期签发方并返回条数，删除后查询抛 CRL_NO_BASE；十一、is_revoked 命中时返回 revoked、reason、revoked_at 和来源 base 或 delta，未命中返回四个字段全为空；十二、stats 记录 bases、deltas、rejected、queries、hits 并可用 reset_stats 归零，snapshot 输出 version、issuers、bases、deltas、stats，必须是深拷贝，restore 结构非法抛 CRL_SNAPSHOT_INVALID 且原状态不变。

硬约束：提交 base 或 delta 全程持锁且先校验后写入，任一校验失败不能留下半份数据；同一张 base 被多个线程同时提交只能有一份成功；快照要能跨进程恢复，恢复前后的后续写入不能互相影响；过期清理与查询之间不能出现同一签发方既已过期又仍能命中的中间态；缓存按签发方设上限，不允许无界增长。

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
