# IPv4 分片重组守卫

## 题面

上周骨干出口的监控连着报了三晚：一台对外提供 UDP 服务的机器在凌晨被灌了大量重叠分片，重组进程的内存曲线一路往上，最后 OOM；同一时间段另一台设备收到分片长度不是 8 的整数倍、offset 却很大的报文，重组逻辑直接把整包当成了完整报文交上去，业务侧解析出一段错位的数据；还有一次更隐蔽，同一 datagram 的不同分片带着不同协议号进来，重组结果被塞进了错误的会话。现在要求把 IPv4 分片重组单独做成守卫模块，重叠、越界、超时和表容量全部按硬规则处理。范围是 gsb/batch47-safety-c-08 下的 ipv4_fragment_guard.py，类 Ipv4ReassemblyGuard。

公开接口：`Ipv4ReassemblyGuard(*, max_datagram_bytes: int = 65535, max_fragments: int = 64, timeout: int = 30, max_pending: int = 128, clock=None)`；`accept(fragment: dict, *, now=None) -> dict`；`datagram(src: str, dst: str, ident: int) -> dict | None`；`pending_count() -> int`；`pending_keys() -> list`；`bytes_held() -> int`；`expire(now=None) -> int`；`discard(src: str, dst: str, ident: int) -> bool`；`limit_source(prefix: str, *, max_pending: int) -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败抛 FragmentError(code, detail)，code 用下面列出的字符串。

分片字典的字段是 src、dst、id、protocol、offset、payload、mf：offset 以 8 字节为单位，payload 是字符串，mf 表示还有后续分片。故障与边界：一、src 或 dst 不是合法 IPv4 字面量抛 FRAG_INVALID_ADDRESS，id 不在 0 到 65535 抛 FRAG_INVALID_ID，protocol 不在 1 到 255 抛 FRAG_INVALID_PROTOCOL；二、payload 为空抛 FRAG_BAD_LENGTH，mf 为真但长度不是 8 的整数倍同样抛 FRAG_BAD_LENGTH；三、offset 乘 8 加长度超过 max_datagram_bytes 抛 FRAG_TOO_LARGE；四、同一 src、dst、id 组合第一次出现返回 partial，带上分片数和累计字节，datagram 能查到协议、分片数、累计字节、已知总长和首片时间；五、分片可以乱序到达，只有从 0 开始连续覆盖到一个 mf 为假的分片末尾才算完整，完整时返回 complete 和 total_len，并从待重组表移除；六、中间有空洞时不得判完整，total_len 只按 mf 为假的分片末尾计算；七、完全相同的分片再次到达返回 duplicate，不重复计字节，也不新增分片；八、同一区间换了 payload，或新区间与已收区间重叠但内容不同，抛 FRAG_OVERLAP，已收分片不动；九、同一组合中途换了 protocol 抛 FRAG_PROTOCOL_MISMATCH；十、单包分片数达到 max_fragments 后继续来新分片抛 FRAG_LIMIT 并把整个待重组项丢弃；十一、待重组项数达到 max_pending 时新组合抛 FRAG_TABLE_FULL，已有组合仍可继续；十二、limit_source 按前缀限制该源同时存在的待重组项数，超限抛 FRAG_SOURCE_LIMIT，不影响前缀之外的源；十三、expire 删除首片时间加 timeout 小于等于 now 的项并返回条数，accept 碰到这种陈旧项要先按过期回收再当作新组合处理；十四、snapshot 必须深拷贝待重组项、前缀限制和统计，restore 结构非法抛 FRAG_SNAPSHOT_INVALID 且原状态不变。

硬约束：accept、expire、discard、restore 全程持锁，八个线程同时投递同一个 datagram 的八个互不相同的分片只能产生一次 complete，其余全是 partial；快照与运行状态彻底分离；待重组项数、单包分片数、单包字节数、按源待重组数全部有硬上限；stats 精确区分 accepted、duplicates、completed、rejected、dropped、expired，reset_stats 归零。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 地狱
