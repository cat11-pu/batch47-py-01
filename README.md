# 题面

接入层网关这两个月一直背 HTTP/1.1 分帧的锅。安全组在例行评估里判定我们存在请求走私风险，要求一周内给边缘代理补上一层严格的分帧校验。故障现象是同一段字节流经过不同节点后，后端看到的请求边界不一致：有节点只按 Content-Length 截断，有节点按 chunked 解码，攻击者于是把两条请求塞进一个报文，第二条绕过前置鉴权直接落到后端缓存；还有客户投诉浏览器发出的重复 Content-Length 被我们判成非法，长连接被无辜掐断；晚上扩容滚动重启后，连接里没解析完的半条报文被丢掉，客户端重试又触发了一次重复下单。运维希望这套校验能自己记录计数、能在重启前后接上状态，并且不允许被慢速大包拖垮内存。

范围是 gsb/batch47-safety-d-06 下的 http1_framing_guard.py，类 FramingGuard。公开接口（Python 类型注解）：`FramingGuard(*, max_header_bytes: int = 8192, max_body_bytes: int = 1048576, max_pipeline: int = 16, clock=None)`；`begin_connection(conn_id: str, *, peer: str) -> dict`；`close_connection(conn_id: str) -> None`；`feed(conn_id: str, data) -> list`；`buffered_bytes(conn_id: str) -> int`；`outstanding(conn_id: str) -> int`；`complete_response(conn_id: str, *, status: int) -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 FramingError(code, detail)，code 取下面列出的字符串。

故障与边界：一、feed 只吐出本次已经完整的请求，每条含 method、target、version、headers、trailers、body、framing、content_length、seq；headers 的键一律小写，同名头按出现顺序用逗号加空格合并；未完整的字节留在连接缓冲区，buffered_bytes 返回其长度；二、Content-Length 与 Transfer-Encoding 同时出现，或 TE 序列不是且仅是 chunked，或重复 Content-Length 数值不一致，抛 HTTP_SMUGGLING_RISK；数值一致的重复 CL 归一化后接受；三、chunked 解码支持分号扩展，块长只接受十六进制数字，带 0x 或正负号抛 HTTP_BAD_CHUNK，块数据后必须是 CRLF，解码总量超过 max_body_bytes 抛 HTTP_BODY_TOO_LARGE，trailer 中出现 content-length、transfer-encoding 或 host 抛 HTTP_SMUGGLING_RISK；四、Host 必须恰好一个且非空，否则抛 HTTP_BAD_HOST，absolute-form 请求目标的主机名忽略端口后必须与 Host 相同；五、以空格或 Tab 续行的折叠头抛 HTTP_HEADER_FOLDING，头名不是合法 token 或头值含控制字符抛 HTTP_BAD_HEADER，请求行不是三段、方法不是全大写字母、版本不是 HTTP/1.1 都抛 HTTP_MALFORMED_REQUEST_LINE；六、头块超过 max_header_bytes 抛 HTTP_HEADER_TOO_LARGE；任何一次校验失败都把该连接置为中毒并清空缓冲，之后 feed 与 complete_response 抛 HTTP_CONNECTION_POISONED，rejected 只加一次；七、未应答请求数超过 max_pipeline 抛 HTTP_PIPELINE_OVERFLOW；complete_response 返回 answered 与 outstanding，队列为空抛 HTTP_NO_PENDING，status 不是一百以上的整数抛 HTTP_BAD_STATUS；八、相同 conn_id 与 peer 重复注册幂等返回 existing 为 True，peer 不同抛 HTTP_CONNECTION_CONFLICT，未知连接上的任何操作抛 HTTP_UNKNOWN_CONNECTION；九、stats 固定返回 connections、requests、responses、rejected、bytes_in 五个计数，reset_stats 只把计数归零、不关闭连接；十、snapshot 必须深拷贝每个连接的 peer、未解析缓冲、outstanding、序号、answered、中毒标记以及统计，restore 之后缓冲还能继续拼出下一条请求且 seq 接着递增，结构非法抛 HTTP_SNAPSHOT_INVALID 且原状态不变；十一、慢速大包只能占住 max_header_bytes 与 max_body_bytes 预算，超限即断开，不允许缓冲无界增长。

硬约束：同一连接的 feed 必须串行，八个线程同时投递整条请求时全部解析成功且 seq 互不重复；并发 complete_response 不能重复递减同一个配额；快照必须能跨进程恢复，恢复后已计数的请求不重算、半条报文不丢；begin_connection 的幂等语义在并发下同样成立。

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
