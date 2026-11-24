# 音频素材入库与去重

## 题面

音频素材库这周被运营灌了两批素材，问题集中爆发：有人把 24 位立体声的 WAV 传上来，系统按 16 位单声道算，缩略时长差了三分之一；同一段环境音换个文件名又传一遍，库里多出七份，磁盘一下告急；还有几个下载到一半的文件把入库任务卡死，接口超时但没有报错码；服务重启后标签全丢，按标签检索直接空结果。入库逻辑在 `gsb/batch47-game-g-04` 的 `audio_asset_registry.py`，类 `AudioAssetRegistry`，负责 WAV 体检、内容去重、标签和容量。

对外接口（Python 类型注解）：
`AudioAssetRegistry(*, max_assets: int = 32, max_bytes: int = 1_000_000, max_chunks: int = 16, allowed_sample_rates=None)`；
`register(asset_id: str, data, *, tags=()) -> dict`；
`get(asset_id: str) -> dict`；`remove(asset_id: str) -> dict`；`list_assets(*, tag: str | None = None) -> list`；
`add_tag(asset_id: str, tag: str) -> dict`；`remove_tag(asset_id: str, tag: str) -> dict`；
`verify(asset_id: str) -> dict`；`usage() -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。
校验失败抛 `AssetError(code, detail)`。

故障与边界：

一、构造参数 `max_assets`、`max_bytes`、`max_chunks` 必须为正；`allowed_sample_rates` 为 `None` 表示不限制，传入空元组表示没有任何采样率可通过，按配置错误处理，统一抛 `ASSET_CONFIG_INVALID`。

二、`data` 必须是字节串，否则抛 `ASSET_BAD_BYTES`；不足 12 字节、RIFF 或 WAVE 标识不对抛 `WAV_HEADER_INVALID`；RIFF 声明长度超出实际字节抛 `WAV_TRUNCATED`。

三、按 RIFF 规则遍历 chunk：每块 4 字节标识加 4 字节小端长度，长度为奇数的块后面必须有一个填充字节；块声明长度超过剩余字节抛 `WAV_TRUNCATED`；块的数量（含未知块）超过 `max_chunks` 抛 `WAV_CHUNK_LIMIT`；未知块可以跳过，但不能绕过计数。

四、必须同时存在 `fmt ` 和 `data` 块，缺任意一个抛 `WAV_CHUNK_MISSING`；`fmt ` 长度不足 16 字节按头部错误处理。

五、只接受 PCM（格式 1）和 IEEE 浮点（格式 3），其他格式抛 `WAV_UNSUPPORTED_FORMAT`；声道数必须在 1 到 8、位深必须是 8、16、24、32 之一、`block_align` 必须等于声道数乘位深除以 8，否则抛 `WAV_BAD_FORMAT`；配置了允许采样率且文件采样率不在名单内抛 `WAV_SAMPLE_RATE_REJECTED`。

六、`data` 长度为 0 或不足一个完整帧抛 `WAV_EMPTY_PAYLOAD`；帧数按数据长度整除 `block_align` 计算，尾部不足一帧的字节忽略，时长按帧数乘 1000 整除采样率向下取整。

七、登记成功返回 `status` 为 `registered`、`asset_id`、`canonical_id`、`duration_ms`、`frames`、`sample_rate`、`channels`、`bits`、`bytes`、`sha256`、`tags`（按字典序）；`get` 返回同样字段，`add_tag` 与 `remove_tag` 返回 `asset_id` 和排序后的 `tags`。

八、按内容 SHA-256 去重：相同字节再次上传返回 `status` 为 `duplicate`、本次 `asset_id` 和首次登记的 `canonical_id`，不新增条目、不占容量、不重复计字节；重复登记的新 `asset_id` 之后查询按未知素材处理。

九、素材数达到 `max_assets` 或加入后总字节超过 `max_bytes` 抛 `ASSET_LIBRARY_FULL`，不得写入半条记录。

十、标签：登记时给的标签去重排序；`add_tag` 对已存在的标签幂等；`remove_tag` 删除不存在的标签抛 `ASSET_UNKNOWN_TAG`；所有针对未登记素材的 `get`、`add_tag`、`remove_tag`、`verify`、`remove` 都抛 `ASSET_UNKNOWN_ASSET`；`list_assets` 按 `asset_id` 升序，带 `tag` 时只列包含该标签的素材。

十一、`remove` 返回 `removed` 与 `bytes_freed` 并释放容量；`verify` 重新计算摘要并返回 `valid` 为 `True`、`sha256`、`duration_ms`、`asset_id`。

十二、`snapshot`/`restore` 必须带上素材字节、摘要、探测结果、标签和计数，快照结构为 `{"version": 1, "assets": {...}, "stats": {...}}` 且是深拷贝；结构非法、内容无法解码或探测失败都抛 `ASSET_SNAPSHOT_INVALID`，原状态保持不变；`usage` 键为 `assets`、`bytes`、`max_assets`、`max_bytes`；`stats` 区分 `registered`、`duplicates`、`removed`、`rejected`，`reset_stats` 全部归零。

硬约束：登记、删除、标签、恢复全程持锁，8 个线程同时上传同一份字节只能有一个 `registered`，其余全部 `duplicate` 且 `canonical_id` 相同；素材数和字节数是硬上限，去重不能绕过计数；重启恢复后标签、摘要和容量占用必须与恢复前一致。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- difficulty: 困难
