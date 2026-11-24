# 任务说明

周五下午支付团队发现自己的 feature 分支构建产物里混进了主分支的凭据文件，同一周另一个拉取请求把自己的缓存写进公共 key，合并后主分支一直在用被污染的产物。追查下来缓存只按 key 匹配，完全没看写入来自哪条 ref；fork 过来的 PR 能读到目标仓库主分支的缓存，两个不同 PR 之间也能互相覆盖；key 重复写入没有冲突保护，后到的直接顶掉前面的；缓存体积不设上限，磁盘告警连续响了三天；缓存命中率统计仍然按全局累加，无法判断污染来自哪条分支。请修 gsb/batch47-safety-b-07 下的 build_cache_scope_guard.py，公开类型 BuildCacheGuard，异常 CacheScopeError(code, detail)，只改这个文件，不要动 tests 目录。

保持这些公开接口：`BuildCacheGuard(*, max_entries=1000, max_bytes=10_000_000, default_ttl=3600, clock=None)`；`register_project(project, *, default_branch, visibility="private") -> dict`；`describe_project(project) -> dict`；`put(project, key, *, scope, size, digest, ttl=None, now=None) -> dict`；`get(project, key, *, scope, now=None) -> dict`；`restore_lookup(project, prefix, *, scope, limit=5, now=None) -> list`；`invalidate(project, *, key=None, scope=None) -> int`；`purge_expired(*, now=None) -> int`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。

故障与边界：一、项目重复注册时 default_branch 与 visibility 一致则幂等，任一不同抛 CACHE_CONFLICT，未登记项目抛 CACHE_UNKNOWN_PROJECT；二、scope 只接受 refs/heads/... 或 refs/pull/<数字>/merge 形式，key 限 1 到 128 位的字母数字与 ._/-，digest 必须是 sha256: 加 64 位小写十六进制，size 与 ttl 必须为正，否则分别抛 CACHE_BAD_SCOPE、CACHE_BAD_KEY、CACHE_BAD_DIGEST、CACHE_BAD_SIZE、CACHE_BAD_TTL；三、同一项目同一 scope 同一 key 重复写入相同 digest 幂等返回 exists，不同 digest 抛 CACHE_KEY_CONFLICT 并保留原条目；四、拉取请求 scope 的条目只能被同一 scope 读取，主分支和其他分支读取都抛 CACHE_SCOPE_DENIED；五、主分支条目可被任意非 PR scope 读取，其他分支条目可被主分支读取，两条 feature 分支之间互不可见，越权读取抛 CACHE_SCOPE_DENIED；六、restore_lookup 只返回前缀匹配、当前 scope 可读且未过期的条目，按创建时间从新到旧排序，limit 限 1 到 20，否则抛 CACHE_BAD_LIMIT；七、条目在 expires_at 当刻即失效，get 抛 CACHE_MISS 并顺手清除；八、put 前先清理过期条目，条目数达到 max_entries 或字节数将超过 max_bytes 时按最久未使用顺序淘汰，单条 size 超过 max_bytes 直接抛 CACHE_TOO_LARGE；九、purge_expired 返回清理条数，invalidate 按 key、scope 或整个项目删除并返回条数；十、describe_project 返回条目数和字节数；十一、snapshot 覆盖 version、projects、entries、stats、tick，必须深拷贝，恢复后 tick 继续递增以免淘汰顺序被打乱，restore 遇到结构非法抛 CACHE_SNAPSHOT_INVALID 且原状态不变；十二、stats 记录 writes、hits、misses、evictions、rejected，reset_stats 归零。

硬约束：put 的配额检查、淘汰和写入必须在一个锁内完成，并发写同一个 key 只能有一个 stored，其余全部冲突；字节账目不允许出现负数或超过 max_bytes，淘汰与写入之间不能出现条目数或字节数为负的瞬间；快照恢复到新进程后条目、过期时间、使用顺序和统计都要能继续使用；缓存总量按条目数与字节数双重封顶。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复要自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 困难
