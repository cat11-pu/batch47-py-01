# GSB 任务包

## 题面

双十一预热当晚，回源链路出了事故：网关把 HTTP/2 前端请求改写成 HTTP/1.1 发给商品服务，库存接口却把上一个租户的购物车内容返回给了下一个人。追查发现，两条 Content-Length 一条写 5 一条写 6 的请求被原样转发，后端把后随的请求当成前一条的请求体吸收；Transfer-Encoding 与 Content-Length 同时出现也放行；chunked 前带 gzip 的写法把剩余流量留在连接上；头部值里的 CRLF 原样透传，回源日志凭空多出一条 X-Forwarded-For；以空格开头的折叠头部被展开成合法字段；普通头部之后再出现 :path 仍然接受；解析失败的连接被标成干净连接，下一个租户复用时收到错位响应。运维还发现单条连接无上限复用，回源文件描述符被打满，滚动重启后已被隔离的连接重新变成可用。

范围是 gsb/batch47-safety-d-02 下的 http2_request_smuggling_guard.py，类型 RequestSmugglingGuard，负责后端登记、连接复用账本、请求规范化、转发准入、连接隔离与快照恢复。公开接口（Python 类型注解）：`RequestSmugglingGuard(*, max_header_bytes: int = 16384, max_headers: int = 64, max_streams: int = 100, idle_ttl: int = 30, clock=None)`；`register_backend(name: str, *, address: str, protocol: str = "http/1.1", max_streams: int | None = None) -> dict`；`open_connection(conn_id: str, *, backend: str, peer: str, now=None) -> dict`；`normalize(headers, *, method: str = "GET", target: str = "/", authority: str | None = None) -> dict`；`admit(conn_id: str, request: dict, *, now=None) -> dict`；`release(conn_id: str, *, clean: bool = True, now=None) -> dict`；`close_connection(conn_id: str, *, reason: str = "client", now=None) -> dict`；`quarantine(conn_id: str, *, reason: str) -> dict`；`recycle(now=None) -> int`；`connection_state(conn_id: str) -> dict`；`backend_status(backend: str) -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 SmugglingError(code, detail)。normalize 结果固定为 method、target、authority、scheme、headers、content_length、chunked、framing 八个字段，framing 取 chunked、content-length、none；admit 返回 conn_id、stream_id、backend、status、reused 五个字段；release 与 close_connection 返回 conn_id、state、streams；backend_status 返回 backend、address、protocol、max_streams、connections，connections 统计未关闭连接；stats 固定 forwarded、rejected、opened、closed 四个计数。

故障与边界：一、后端名重复且地址、协议或流上限任一不同抛 SMUGGLE_CONFLICT，完全相同幂等返回，空地址、协议不在 http/1.1 与 http/2、非正流上限抛 SMUGGLE_BAD_BACKEND；二、未登记后端开户抛 SMUGGLE_UNKNOWN_BACKEND，同一 conn_id 绑到不同后端抛 SMUGGLE_CONN_CONFLICT，同后端重复开户幂等且不改 opened_at、peer 与流计数；三、content-length 重复但取值相同折叠为一条，取值不同抛 SMUGGLE_LENGTH_CONFLICT，非纯数字、负数或带逗号抛 SMUGGLE_BAD_LENGTH；四、transfer-encoding 只接受恰好一个 chunked，gzip 组合、重复 chunked、其他编码抛 SMUGGLE_TE_AMBIGUITY，与 content-length 同时出现抛 SMUGGLE_TE_CL_MISMATCH；五、头部值以空格或制表符开头抛 SMUGGLE_HEADER_FOLD，值含 CR、LF、NUL 抛 SMUGGLE_CTRL_CHAR，头部名去空白转小写后不是合法 token 或为空抛 SMUGGLE_BAD_HEADER_NAME；六、伪头部只允许 :method、:path、:scheme、:authority 各一次且必须在普通头部之前，违反顺序抛 SMUGGLE_PSEUDO_ORDER，未知伪头部、:status、重复或空值抛 SMUGGLE_BAD_PSEUDO；七、头部条数超过 max_headers 抛 SMUGGLE_TOO_MANY_HEADERS，名称与值按字节累计再加每项 2 字节分隔后超 max_header_bytes 抛 SMUGGLE_HEADER_TOO_LARGE，两项都在写入任何状态前判定；八、admit 只接受 normalize 结果，请求结构缺失抛 SMUGGLE_BAD_REQUEST，流号从 1 递增，第二次起 reused 为 true；九、连接流预算达到 stream_limit 后继续 admit 抛 SMUGGLE_STREAM_LIMIT 且不增计数，换一条新连接重新从 1 开始；十、release 只在 open 状态连接上执行，重复 release 或对 idle、poisoned 连接执行抛 SMUGGLE_BAD_STATE，clean 为 false 时连接转 poisoned 并记录原因；十一、poisoned 连接 admit 抛 SMUGGLE_CONN_POISONED，closed 连接抛 SMUGGLE_CONN_CLOSED，重复关闭同样抛 SMUGGLE_CONN_CLOSED，未知连接统一抛 SMUGGLE_CONN_UNKNOWN；十二、recycle 只关闭空闲时长达到 idle_ttl 的 idle 连接并返回条数，恰好等于阈值算超时，同一时刻重复调用返回 0，有在途流的连接永不回收。

硬约束：admit、release、close_connection、quarantine、recycle 全程持同一把锁，八线程在同一连接上抢五个流预算只能发出五条，其余全部 SMUGGLE_STREAM_LIMIT，流号不得重复或跳号；snapshot 深拷贝后端、连接账本与统计，键为 version、backends、connections、stats，restore 后毒化状态、流计数与统计原样恢复且后续流号接着递增，结构非法或版本不符抛 SMUGGLE_SNAPSHOT_INVALID 且原状态一字不动；头部缓冲、连接数与每连接流数都是硬上限，connection_state 与 backend_status 只读，不得改变状态或触发回收；normalize 与 admit 的每次拒绝各计一次 rejected，关闭与回收各计一次 closed。

验收：`python3 -m pytest -q tests` 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 困难
- name: batch47-safety-d-02
- module: http2_request_smuggling_guard
- typeName: RequestSmugglingGuard
