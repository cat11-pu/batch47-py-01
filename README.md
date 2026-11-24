# GSB 任务

## 题面

共享构建集群上，一条被污染的缓存对象让三个服务的发布流水线连续失败了两天。复盘发现：来自 fork 的 pull request 构建能读到主分支的受保护缓存，还能把自己产出的对象写回同名键；同一缓存键先写了一份摘要不同的内容，后写的那份直接覆盖并且没有任何告警；仓库配额被打满之后新对象全部失败，但过期对象一直没被清理；两个 PR 的缓存键相同却互相覆盖；TTL 到期的对象仍被 resolve 命中；批量关闭 PR 时把主分支缓存一起删了。

范围是 gsb/batch47-safety-b-04 下的 ci_cache_guard.py，类 CiCacheGuard，负责仓库登记、作用域派生、缓存读写、晋级、配额与过期清理。公开接口：CiCacheGuard(*, max_entries: int = 256, max_bytes: int = 4 * 1024 * 1024, max_entry_bytes: int = 1024 * 1024, default_ttl: int = 3600, max_ttl: int = 7 * 24 * 3600)；register_repository(repo: str, *, protected_branches) -> dict；open_scope(repo: str, *, ref: str, event: str, fork: bool = False) -> dict；put(scope_id: str, *, key: str, data, digest: str | None = None, ttl: int | None = None, now=None) -> dict；get(scope_id: str, *, key: str, now=None) -> dict | None；resolve(scope_id: str, *, key: str, now=None) -> dict | None；promote(scope_id: str, *, key: str, target_scope_id: str, now=None) -> dict；close_scope(scope_id: str) -> int；evict(now=None) -> int；entries(scope_id: str | None = None) -> list；snapshot() -> dict；restore(state: dict) -> None；stats() -> dict；reset_stats() -> None。失败统一抛 CacheError(code, detail)。

故障与边界：一、仓库名非法抛 CACHE_INVALID_REPO，保护分支必须是完整 refs/ 路径否则 CACHE_INVALID_REF，同仓库换一套保护分支抛 CACHE_CONFLICT，配置相同幂等；二、push 到保护分支得到 protected 作用域且 trusted=True，普通分支是 branch，pull_request 是 pr，fork 即使 event 为 push 也降级为 fork，两者 trusted 均为 False，未知仓库、未知事件、非法 ref 分别报对应错误；三、缓存键只允许字母数字和点、下划线、斜杠、短横，最长 128，否则 CACHE_INVALID_KEY；内容必须是字节且非空，超过 max_entry_bytes 抛 CACHE_ENTRY_TOO_LARGE，TTL 非正或超过 max_ttl 抛 CACHE_TTL_INVALID；四、显式传入的摘要必须等于内容 SHA-256，否则 CACHE_DIGEST_MISMATCH；五、同一作用域同一键同摘要重复写入返回 status=unchanged，摘要不同抛 CACHE_KEY_CONFLICT 并保留原值；六、branch 作用域读取缺失键时可以向同仓库 protected 作用域回退并把 fallback 置为 True，pr 与 fork 一律不回退，protected 也不回退；七、promote 只允许可信作用域向同仓库 protected 作用域晋级，跨仓库、从 pr 晋级、目标不是 protected 都抛 CACHE_SCOPE_DENIED，源键不存在抛 CACHE_NOT_FOUND，目标同键不同摘要抛 CACHE_KEY_CONFLICT；八、close_scope 只清理 pr 与 fork，对 protected 与 branch 抛 CACHE_SCOPE_DENIED，返回删除条数；九、过期按 now 与 expires_at 比较，get 命中过期对象返回 None 并计入淘汰，evict 返回清理数量；十、仓库字节配额超过 max_bytes 时先清过期，再按最近最少使用淘汰 pr 与 fork，然后淘汰 branch，protected 不因配额删除，仍不够抛 CACHE_QUOTA_EXCEEDED；十一、全局条目数达到 max_entries 抛 CACHE_CAPACITY_FULL；十二、snapshot 深拷贝仓库、作用域、条目内容、字节账目与统计，恢复后可信关系和隔离规则不变，结构非法抛 CACHE_SNAPSHOT_INVALID 且原状态不变。

硬约束：put、promote、resolve 与配额淘汰全程持锁，多个线程同时写同一键同摘要只能落盘一次，其余返回 unchanged；同键不同摘要的并发写入只能有一个成功，其余必须冲突；缓存总量受 max_entries 与 max_bytes 双重约束，不允许因重试无界增长；stats 区分 put、hit、fallback、evicted、conflict、rejected，reset_stats 归零。

验收：python3 -m pytest -q tests 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发写入与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 困难
- module: ci_cache_guard
- typeName: CiCacheGuard
