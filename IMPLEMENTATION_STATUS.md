# 生产化执行状态

## 已完成

| 项目 | 状态 | 实现 |
|---|---|---|
| 架构拆分 | 完成 | `music_app` 分为 models/errors/catalog/downloads/media/player/storage/config/logging/ui/CLI |
| 授权门禁 | 完成技术门禁 | GUI 必须勾选授权确认；CLI 必须传 `--accept-rights`；显式不可下载及 DRM 状态会拒绝 |
| 搜索稳定性 | 完成 | TTL 缓存、最小请求间隔、取消令牌、重复结果过滤、域名校验 |
| 下载并发 | 完成 | 可配置并发数，默认 3；每个任务独立状态和取消令牌 |
| 文件唯一性 | 完成 | 文件名追加 track ID，避免同名覆盖 |
| 下载索引 | 完成 | SQLite 保存 track ID、SHA-256、路径、源地址、格式和质量信息 |
| 音频校验 | 完成 | Mutagen 校验时长、采样率、声道和码率 |
| 试听缓存 | 完成 | 容量上限、LRU 淘汰、原子替换、切换取消 |
| 播放器 | 完成基础抽象 | Pygame 后端封装为可替换 `AudioPlayer`，支持播放、暂停、停止和 Seek |
| 配置持久化 | 完成 | `work/config.json`，原子写入和 0600 权限 |
| 日志 | 完成 | JSON Lines、轮转、URL/Token 脱敏 |
| 错误分类 | 完成 | DRM、权限、429、网络、磁盘满、取消等错误码 |
| 依赖锁定 | 完成 | 精确运行依赖和 macOS arm64 SHA-256 锁 |
| 静态检查 | 完成 | Ruff、Mypy 全部通过 |
| 自动化测试 | 完成 | 117 个离线测试和 2 个实网测试通过，总覆盖率 92.51% |
| CI | 完成 | GitHub Actions：Ruff、Mypy、pytest 和 90% 覆盖率门禁 |

## 部分完成

| 项目 | 差距 | 下一步 |
|---|---|---|
| 流式播放 | 当前仍先生成受容量限制的临时 MP3，再交给 Pygame | 接入 libmpv/Qt Multimedia，直接播放 HLS 并 Seek |
| 音质策略 | 为满足 M4A 容器可对 MP3 源做有损转换 | 默认保留源格式；仅在用户明确选择容器时转换并提示 |
| Cookie/代理 | CLI 已支持，GUI 尚未完整开放 | GUI 增加配置页，Cookie 存入系统钥匙串 |
| 打包发布 | 尚无 `.app`、签名和公证 | PyInstaller/py2app + codesign + notarytool |
| 更新机制 | 未实现 | 签名清单、增量更新和版本迁移 |
| 遥测与崩溃报告 | 无 | 用户主动同意后启用脱敏诊断 |

## 外部发布门禁

- SoundCloud API、服务条款和音乐版权的正式授权。
- 可下载曲目的判定规则必须以平台/版权方数据为准。
- Apple Developer ID、代码签名和公证证书。
- 商业发布前完成隐私政策、用户协议和版权投诉流程。

代码质量门禁不能替代上述合同、授权和账户权限。

## 当前验证命令

```bash
ruff check music_app tests soundcloud_gui.py soundcloud_downloader.py
mypy music_app --ignore-missing-imports
SDL_AUDIODRIVER=dummy pytest -m "not network"
RUN_NETWORK_TESTS=1 pytest tests/test_live_soundcloud.py --no-cov
```
