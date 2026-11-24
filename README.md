# 共享缓存策略纠偏

## 题面

边缘缓存节点上线两周，值班表上每天都有人来问同一个页面的内容为什么忽新忽旧。运维复现出的现象很杂：同一份静态资源被两个上游同时回源，缓存里出现两份互相矛盾的副本；带 Authorization 头的接口响应被存进共享缓存，下一个匿名请求直接拿到了别人账号下的数据；页面按 Accept-Encoding 分成 gzip 与 br 两个版本，缓存键却算成了同一个，br 客户端收到 gzip 字节后白屏；源站把 Vary 写成 cookie 时缓存照存不误，随后就发生了跨用户串号；回源路径里带 .. 的请求被原样拼进缓存键，缓存被写进了不该写的命名空间；促销期间一个大对象顶穿内存上限，进程被拖到 OOM；容量写满后新对象一律被拒，可缓存里一大半早就过了回收窗口；304 重新验证之后新鲜期没有顺延，用户每隔一分钟就被打回源站。这块逻辑在 gsb/batch47-safety-d-01 的 http_shared_cache_guard.py，类 HttpSharedCacheGuard。

它管的是共享缓存的新鲜度、变体划分、失效与回源合并。构造签名 HttpSharedCacheGuard(*, max_entries=1000, max_object_bytes=1000000, default_ttl=60, stale_window=30, clock=None)。需要提供的接口：register_origin(origin, *, vary_headers=(), allow_authorized=False, max_ttl=3600) -> dict；store(request, response, *, now=None) -> dict；lookup(request, *, now=None) -> dict；revalidate(request, *, now=None, etag=None) -> dict；invalidate(origin, *, path_prefix="/", now=None) -> int；purge(now=None) -> int；begin_fill(request) -> dict；end_fill(request, *, token) -> None；stats()、reset_stats()、snapshot()、restore(state)。校验失败统一抛 HttpCacheError(code, detail)。

要覆盖的故障与边界：一、lookup 必须区分 hit、stale、miss 三态，hit 返回剩余 TTL，stale 返回已过期秒数；二、响应带 no-store 或 private、或请求带 Authorization 而该源站未放行时，返回 stored 为 False 与原因，既不写缓存也不抛异常；三、未登记源站抛 HTTP_CACHE_ORIGIN_UNKNOWN，路径不以斜杠开头或含 ..、分号、反斜杠、问号时抛 HTTP_CACHE_BAD_PATH；四、对象字节数超过 max_object_bytes 抛 HTTP_CACHE_OBJECT_TOO_LARGE，且缓存保持原样；五、查询参数按名字排序归一化，路径必须逐字符相等；六、响应 Vary 只允许出现源站声明过的名字，出现星号或未声明名字抛 HTTP_CACHE_VARY_UNSUPPORTED；七、TTL 先取 s-maxage，没有则取 max-age，再没有取默认值，最后被源站 max_ttl 截断；八、新鲜期结束的那一刻算 stale 而不是 hit，超过 stale_window 后条目被清除并返回 miss；九、容量满时先清理已到期条目，仍然满才抛 HTTP_CACHE_FULL，被拒的对象不写入；十、revalidate 在 etag 相等时返回 not_modified 并顺延新鲜期，不等时返回 modified 并立刻把该条目降级为 stale，条目不存在返回 miss；十一、invalidate 按前缀删除并返回条数，purge 按到期时间删除并返回条数；十二、begin_fill 对同一缓存键重复调用抛 HTTP_CACHE_FILL_IN_PROGRESS，end_fill 的 token 对不上抛 HTTP_CACHE_UNKNOWN_FILL。

硬约束：store、lookup、revalidate 与占位记账必须持同一把锁，八个线程同时写入容量为四的缓存只能有四个成功；snapshot 必须是深拷贝，覆盖源站、条目、占位与统计，非法结构抛 HTTP_CACHE_SNAPSHOT_INVALID 且原状态不变，恢复后旧条目继续按新鲜度判定，恢复前后外部对快照字典的改动都不能影响实例；缓存条目总数与单对象大小都不允许无界增长；stats 要精确区分命中、未命中、过期命中、写入与淘汰，reset_stats 归零。

验收：python3 -m pytest -q tests 的 15 条必须全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 困难
