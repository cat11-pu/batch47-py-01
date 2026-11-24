# 容器镜像层解包安全校验

## 题面

镜像仓库这两个月连续出事。安全组复核一个第三方基础层时发现，解包会把 `../../etc/ld.so.preload` 原样写到宿主目录；另一个层用符号链接指向 `/etc`，后续文件顺着链接覆盖宿主配置；还有层里塞了字符设备节点和带 setuid 位的二进制，落盘后普通用户可以直接提权；一个恶意层声明几十 GB 大小并夹带几万个条目，把 unpack 进程拖死；同一层带不同摘要被导入两次，台账里出现两份；某层本应删除 `/conf/app.conf`，`.wh.` 标记却被当成普通文件名留在镜像里；上层用 opaque 标记清理目录时，下层文件仍有残留；解包中途校验失败，已写进去的文件没有回滚；宿主重启后未完成台账丢失，已提交的层被重复解包；两个 worker 同时提交不同层，目录树互相覆盖，排查时连是哪一层带进来的都定位不到。相关代码在 gsb/batch47-safety-e-01 的 layer_unpack_guard.py，类 LayerUnpackGuard，只用 Python 标准库，失败统一抛 LayerError(code, detail)。

接口保持：`__init__(self, *, root: str = "/layers", max_entries: int = 10000, max_bytes: int = 1 << 30, max_path_depth: int = 32)`；`begin_layer(self, layer_id: str, *, digest: str | None = None) -> dict`；`add_entry(self, layer_id: str, entry: dict) -> dict`；`commit_layer(self, layer_id: str) -> dict`；`abort_layer(self, layer_id: str) -> bool`；`resolve_path(self, raw: str) -> str`；`list_files(self) -> list[str]`；`lookup(self, path: str) -> dict | None`；`layer_state(self, layer_id: str) -> dict`；`stats(self) -> dict`；`snapshot(self) -> dict`；`restore(self, state: dict) -> None`；`reset_stats(self) -> None`。

故障与边界：一、条目名先过 resolve_path，绝对路径抛 LAYER_PATH_ABSOLUTE，任何 `..` 段抛 LAYER_PATH_TRAVERSAL，超过 max_path_depth 抛 LAYER_PATH_TOO_DEEP，空名抛 LAYER_PATH_INVALID，`.` 与重复斜杠做规范化；二、层 ID 非法抛 LAYER_BAD_LAYER_ID，重复 begin 抛 LAYER_DUPLICATE_LAYER，但同一 ID 加相同摘要的已提交层再次 begin 返回 status=already_applied；三、symlink 绝对目标或按父目录计算越界抛 LAYER_SYMLINK_ESCAPE，成环抛 LAYER_SYMLINK_LOOP，落地前必须检查完整条链接链，合法相对目标允许悬空；四、hardlink 目标不存在抛 LAYER_HARDLINK_MISSING，目标不是普通文件抛 LAYER_HARDLINK_INVALID；五、device、fifo 等类型抛 LAYER_UNSUPPORTED_TYPE，缺 name/type 抛 LAYER_ENTRY_INVALID；六、mode 带 setuid、setgid、sticky 位抛 LAYER_UNSAFE_MODE 且不占配额；七、同层重复路径抛 LAYER_DUPLICATE_ENTRY，把已有子路径的目录改成文件抛 LAYER_PATH_CONFLICT，向普通文件底下加子项抛 LAYER_PARENT_NOT_DIR；八、条目数超 max_entries 抛 LAYER_ENTRY_LIMIT，累计字节超 max_bytes 抛 LAYER_SIZE_LIMIT，失败条目不推进计数，配额只按通过校验的条目核算；九、白名单条目提交时删除下层同路径节点，opaque 标记清空该目录下所有下层节点但保留目录本身，commit 返回 written 与 removed 计数；十、commit 原子生效并清空暂存，重复 commit 抛 LAYER_ALREADY_COMMITTED，abort 后视图不变；十一、snapshot 只含已提交视图、层台账和统计，restore 对版本不符或结构非法的状态抛 LAYER_SNAPSHOT_INVALID 且不改动原状态；十二、stats 精确给出 layers_applied、layers_aborted、entries_rejected、whiteouts_applied、bytes_written。

硬约束：add、commit、abort 全程持锁，多线程并发提交不同层时最终视图必须包含所有层且计数准确；快照必须深拷贝，恢复出的守卫要能继续接收新层；任何一步异常都不能留下半成品；暂存条目、字节和统计不得无界增长；同一层只能提交一次。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- difficulty: 地狱
