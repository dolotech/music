# 试听、搜索与打包审查

## 1. 试听无声

### 已检查

- macOS 输出未静音。
- 系统输出音量为 19%。
- 默认输出设备为 `iMac扬声器`。
- `sounddevice` 直接输出 0.8 秒测试音完成，无 PortAudio 错误。
- 真实 SoundCloud 流播放 3 秒时，后台线程保持运行且
  `StreamingPcmBackend.last_error` 为空。
- SoundCloud 搜索结果、FFmpeg PCM 数据和音频设备初始化均可工作。

### 根因判断

应用最终输出链路没有报错。最可能的原因依次为：

1. 用户仍运行在安装流式后端之前启动的旧进程。
2. 系统输出音量只有 19%，实际声音很小。
3. macOS 当前默认输出设备不是用户正在使用的耳机或扬声器。
4. 旧版本回退到了 Pygame 缓存播放器，但界面没有显示当前后端。

另外发现 SoundCloud 媒体 CDN 偶尔持续数十秒不返回首个 PCM 包。旧 UI 会在
首包到达前就显示“正在播放”并推进时间，但扬声器实际没有数据。现在会显示
“正在缓冲音频流”，12 秒后仍无首包则终止 FFmpeg 并提示失败。

### 已改进

- 启动时明确选择 `StreamingPcmBackend`。
- 播放器状态显示实际输出设备名称。
- 音频线程错误会弹出“音频输出失败”，不再静默结束。
- 流式后端初始化成功后写入结构化日志。

如果重启后仍无声，应首先查看播放器状态中的输出设备是否是当前实际设备。

## 2. 搜索慢

### 实测数据

| 路径 | 结果数 | 冷搜索 | 热缓存 |
|---|---:|---:|---:|
| 原 `yt-dlp scsearch` | 12 | 19.569s | 0.000s |
| 原 `yt-dlp scsearch` | 30 | 73.995s | - |
| 直接 SoundCloud 搜索 API | 12 | 5.388s | - |
| 新快速适配器 | 12 | 1.678s | 0.000s |
| 新快速适配器 | 30 | 1.757s | - |

### 根因

- 旧实现每次搜索都进入 `yt-dlp SearchInfoExtractor`。
- 30 条结果会触发分页和多轮提取，耗时接近线性放大。
- 底层请求只有 socket 超时，没有面向用户的首屏时间预算。
- UI 无法中止已经开始的底层搜索请求。
- 原先没有阶段耗时日志，无法区分网络、提取和排序时间。

### 已完成优化

- 新增直接 SoundCloud 搜索 API 适配器。
- 保留 `yt-dlp` 作为无客户端 ID、鉴权失效时的后备。
- 搜索结果 TTL 缓存提升到 300 秒。
- 直接 API 请求超时限制为 8 秒。
- `yt-dlp` 后备路径最多取 15 条，避免再次进入 30 条的分页慢路径。
- 记录缓存命中和搜索总耗时。
- 搜索后备启用 curl_cffi 浏览器 TLS impersonation。
- 12 条由约 19.6 秒降到约 1.7 秒。
- 30 条由约 74 秒降到约 1.8 秒。

### 后续建议

- 增加“加载更多”，首屏固定 12–15 条。
- 预取并持久化客户端 ID，减少首次冷启动。
- 对同一时间只允许一个搜索请求，旧请求到达后直接丢弃。
- 正式商业版本仍需要 SoundCloud 授权接口。

## 3. 参考 TelePull 的打包方式

TelePull 的构建契约包括：

- Makefile 统一 `deps/run/test/lint/check/package` 入口。
- PyInstaller `onedir`，GUI 与控制台程序分开。
- 使用 `collect_all` 收集动态依赖，关闭 UPX。
- 构建完成后实际运行打包产物做自检。
- GitHub Actions 在目标系统构建并上传 ZIP。
- 标签发布自动上传 Release。
- 正式包预留 codesign/notarization 和质量门禁。

本项目已落地同类结构：

- `Makefile`
- `SoundCloudMusic.spec`
- `tools/build_pyinstaller.py`
- `tools/package_macos.sh`
- `music_app/selftest.py`
- `.github/workflows/build-macos.yml`
- 单目录 GUI `SoundCloudMusic`
- 控制台 `SoundCloudMusicCLI`
- ZIP 构建产物、SHA-256 和构建信息
- 打包后真实执行 `--selftest`

本机验证：

- GUI 和 CLI 均为 arm64 可执行文件。
- PyInstaller 内置 Python、Tk、FFmpeg、yt-dlp、sounddevice 和 pygame。
- 打包产物自检通过。
- `data_dir` 指向用户 Application Support。
- 目录与 ZIP 已生成。

用户给出的具体曲目已复测：

- 媒体信息解析成功，时长约 238.6 秒。
- 真实流输出 1,736,704 字节 PCM，无音频后端错误。
- 相关关键字搜索约 1.9 秒返回 12 条结果。

发布包同时收集 `curl_cffi`、内置 CA 证书（certifi）、Brotli、PortAudio、
SDL、Tk 和 FFmpeg。自检会拒绝使用包外的 Python 或 FFmpeg。

正式发布仍需：

- Apple Developer ID。
- `CODESIGN_IDENTITY` 和 hardened runtime。
- `notarytool` 公证及 stapling。
- 公证完成后的外部下载安装测试。
