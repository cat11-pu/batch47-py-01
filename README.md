# POSIX ACL 继承与访问判定

## 题面

文件网关做权限迁移时，ACL 相关的工单一下子堆起来。开发在目录上配了默认 ACL，新建文件却没继承，落盘后只剩 0644；另一些目录继承了默认 ACL，但 mask 没重算，给 alice 的读写权限实际只剩读；文件上加了命名用户条目后，chmod 一改就把 mask 写成组权限，alice 从可写变成只读；组的判定只看属组，命名组被忽略；同一路径给两个组授权，取交集而不是并集，导致该放行的请求被拒；用 setfacl 清空条目后，旧 mask 还留着，stat 显示 extended；有人在普通文件上设默认 ACL 也返回成功；删目录时非空目录直接消失，审计对不上删除数量；重启后 access 与 default 两套 ACL 全部丢失；两个线程同时给同一文件加不同命名用户，后写的把先写的整份覆盖；还有路径穿越和相对路径混进来，把 ACL 写到树外。相关代码在 gsb/batch47-safety-e-03 的 posix_acl_guard.py，类 PosixAclGuard，只用 Python 标准库，失败统一抛 AclError(code, detail)。

接口保持：`__init__(self) -> None`；`mkdir(self, path: str, *, mode: int = 0o755, uid: int = 0, gid: int = 0) -> dict`；`create_file(self, path: str, *, mode: int = 0o644, uid: int = 0, gid: int = 0) -> dict`；`setfacl(self, path: str, entries, *, default: bool = False) -> dict`；`getfacl(self, path: str, *, default: bool = False) -> list`；`chmod(self, path: str, mode: int) -> dict`；`check_access(self, path: str, *, uid: int, gids=(), want: str = "r") -> bool`；`stat(self, path: str) -> dict`；`remove(self, path: str, *, recursive: bool = False) -> int`；`list_tree(self) -> list`；`stats(self) -> dict`；`snapshot(self) -> dict`；`restore(self, state: dict) -> None`。

故障与边界：一、路径必须是绝对路径、不允许 `..` 段，非法抛 ACL_BAD_PATH；父目录不存在抛 ACL_ENOENT，父不是目录抛 ACL_ENOTDIR，重复创建抛 ACL_EEXIST；二、ACL 条目只接受 user::、user:<数字 ID>:、group::、group:<数字 ID>:、mask::、other:: 六种写法，权限位固定 rwx 三位，格式错误抛 ACL_ENTRY_SYNTAX，未知标签抛 ACL_BAD_TAG，同一标签重复抛 ACL_DUPLICATE_ENTRY；三、带命名条目的 ACL 必须扩展 mask，未显式给出时按命名用户、命名组、属组的权限并集重算，mode 的属主、属组、其他位分别映射 user::、mask、other::；四、创建子项时若父目录有默认 ACL，访问 ACL 继承默认条目并按创建 mode 的类别位过滤后重算 mask，目录的默认 ACL 原样下传；无默认 ACL 时按 mode 生成最小 ACL；五、chmod 更新 user::、other::，扩展 ACL 时把 mask 设为 mode 的组位并保留命名条目，最小 ACL 时更新 group::，不给 ACL 引入新条目；六、setfacl 传入空列表时，访问 ACL 回退为按当前 mode 生成的最小 ACL，默认 ACL 被清空；七、check_access 按属主、命名用户、属组与命名组并集、其他依次判定，扩展 ACL 的组类结果要与 mask 相与，want 只接受 rwx 组合，非法抛 ACL_BAD_PERMISSION；八、默认 ACL 只能设在目录上，否则抛 ACL_NOT_DIRECTORY，目录没有默认 ACL 时查询抛 ACL_NO_DEFAULT；九、remove 对非空目录抛 ACL_NOT_EMPTY，recursive 时返回实际删除节点数，未知路径抛 ACL_ENOENT；十、getfacl 输出固定顺序：user::、命名用户按 ID 升序、group::、命名组按 ID 升序、mask::、other::，最小 ACL 不输出 mask；十一、stat 给出 type、mode、uid、gid、extended，mode 由 ACL 反算；十二、snapshot/restore 覆盖整棵树、访问 ACL、默认 ACL 与统计，结构非法抛 ACL_SNAPSHOT_INVALID 且原状态不变。

硬约束：所有树操作与 ACL 更新持同一把锁，多个线程同时加不同命名条目不得互相覆盖，最终 mask 必须覆盖全部条目；快照深拷贝，恢复后能继续创建子项并按默认 ACL 继承；统计精确区分 files_created、dirs_created、acl_sets、access_checks、denied。

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
