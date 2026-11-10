# SoundCloud 歌曲下载器

脚本位于项目根目录，默认把歌曲保存到 `download/`，并使用 SoundCloud
页面上的歌曲标题加 track ID 作为唯一文件名。默认生成 M4A，例如
`成都 [722635666].m4a`；MP3 转换仍可通过参数启用。

仅在拥有版权、已获授权或版权方明确允许时下载内容，并遵守 SoundCloud
服务条款和当地法律。

## 最简单用法

直接运行 `music.sh` 并传入链接：

```bash
cd /Users/stargame/code/music
./music.sh "https://on.soundcloud.com/5fxCaP8iI3qukbqXC8"
```

文件会保存为：

```text
download/歌曲真实名称 [track_id].m4a
```

`music.sh` 会自动定位项目目录，在首次运行时创建 `.venv` 并安装依赖，
之后显示授权确认。非交互环境必须显式传入 `--accept-rights`。也可以一次
传入多个链接：

```bash
./music.sh --accept-rights "链接1" "链接2"
```

## 图形界面：按歌名搜索

启动桌面界面：

```bash
cd /Users/stargame/code/music
./music_gui.sh
```

界面使用方式：

1. 输入完整歌名或部分关键词，按回车或点击“搜索”。
2. 从候选列表中选择歌曲；列表显示上传者、时长、点赞数、匹配度和下载状态。
3. 点击“试听整首”载入内置播放器；支持播放、暂停、停止和拖动进度条定位。
4. 双击候选只会载入试听；必须手动点击“下载 M4A”才会保存文件。
5. 支持 Command/Ctrl 多选，然后点击“下载选中”批量下载。
6. 勾选“我确认有权下载”后，最多同时下载 3 首；每首歌曲单独显示进度。
7. 文件默认保存到 `download/歌曲真实名称 [track_id].m4a`。

列表顶部可选择“综合排行”“匹配度优先”或“点赞数优先”。界面还支持更改
保存目录和打开 SoundCloud 页面。搜索、试听和下载均在后台运行，窗口不会
卡住；下载失败时会显示具体错误信息。下载可在界面中取消。

搜索默认隐藏时长不超过 35 秒的候选，避免把 SoundCloud 的 30 秒试听条目
当成完整歌曲。需要查看这些条目时，取消勾选“隐藏 30 秒预览”；下载前仍会
再次确认。

界面下载会强制生成 M4A 容器。若 SoundCloud 只提供 MP3 音源，程序会转换
成 M4A；为了兼容格式进行的转换不能提升原始音质。

试听准备期间可以选择另一首并再次点击“切换试听”。程序会取消旧任务、
终止旧 FFmpeg 进程组并删除未完成缓存；“停止”按钮也会立即终止当前试听
传输。下载任务与试听任务使用独立取消令牌，切换或停止试听不会取消下载。

部分 SoundCloud Go+ 或版权方曲目使用 DRM 保护。此类内容无法通过
`yt-dlp` 下载，界面会把状态标记为“DRM 受限”。请选择其他可播放的完整
版本，或使用版权方提供的官方下载渠道。

## 工程化能力

- 搜索采用 TTL 缓存和请求限速。
- 下载、搜索和试听均有独立取消令牌。
- 试听默认采用 FFmpeg → PCM → `sounddevice` 的边下载边播放，不生成完整
  临时歌曲文件；暂停、切歌和停止会终止对应 FFmpeg 进程。
- 如果流式设备不可用，才回退到有容量上限的缓存播放器。
- 下载结束后使用 Mutagen 校验音频，并记录 SHA-256、格式、码率和来源。
- SQLite 索引保存在下载目录的 `.music_index.sqlite3`。
- 配置保存在 `work/config.json`。
- JSON 结构化日志保存在 `work/logs/app.jsonl`，敏感查询参数会脱敏。
- 运行时依赖已精确锁定；macOS arm64 的哈希锁为 `requirements.lock`。

## 手动安装依赖

在项目根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

如果 macOS 报 `CERTIFICATE_VERIFY_FAILED`，使用系统证书安装依赖：

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem python -m pip install -U -r requirements.txt
```

依赖中的 `imageio-ffmpeg` 自带静态 FFmpeg，可自动修复 SoundCloud 常见
的 HLS/M4A 容器问题。

## 高级用法：直接调用 Python 脚本

```bash
python soundcloud_downloader.py \
  --accept-rights \
  "https://on.soundcloud.com/5fxCaP8iI3qukbqXC8"
```

文件会保存为：

```text
download/歌曲真实名称 [track_id].m4a
```

## 可选：转换为 MP3 320 kbps

```bash
python soundcloud_downloader.py \
  --accept-rights \
  "https://on.soundcloud.com/5fxCaP8iI3qukbqXC8" \
  -o download \
  --audio-format mp3 \
  --audio-quality 320 \
  --sample-rate 44100 \
  --channels 2
```

## 批量下载

把多个链接放在命令后面：

```bash
python soundcloud_downloader.py \
  --accept-rights \
  "https://on.soundcloud.com/link-1" \
  "https://on.soundcloud.com/link-2"
```

播放列表页面也可以直接传入。只想下载链接中当前曲目时加
`--no-playlist`。

## 常用选项

```text
--dry-run             只读取歌曲信息，不下载
--no-playlist         只下载当前曲目
--cookies FILE        使用 Netscape 格式 Cookie 文件
--embed-metadata      额外写入标题、作者等音频标签
--force               忽略记录并覆盖同名文件
--proxy URL           指定代理
--audio-format FORMAT 输出格式，默认 M4A
--audio-quality KBPS  转换比特率，默认 320
--sample-rate HZ      转换采样率，默认 44100
--channels NUMBER     转换声道数，默认 2
--accept-rights       确认你有权保存所下载内容
```

文件使用 track ID 保证唯一性。需要覆盖已有文件时使用 `--force`。

注意：把低码率源音频转换为 320 kbps 只能得到符合规格的 MP3 文件，不能
恢复源文件从未包含的声音细节。

## 自动化测试

```bash
source .venv/bin/activate
ruff check music_app tests tools soundcloud_gui.py soundcloud_downloader.py
mypy music_app --ignore-missing-imports
SDL_AUDIODRIVER=dummy pytest -m "not network"
```

默认测试门禁要求总覆盖率不低于 90%。真实 SoundCloud 契约测试默认跳过：

```bash
RUN_NETWORK_TESTS=1 pytest tests/test_live_soundcloud.py --no-cov
```

## 当前生产边界

技术层的模块拆分、依赖锁定、配置、日志、测试、取消、流式播放、文件校验和授权
门禁已经落地。仍有两个发布外部门禁：

1. 需要确认 SoundCloud API/合作授权和可下载内容的许可边界。
2. 需要 Apple Developer 证书完成签名、公证和正式安装包发布。

详见 [PRODUCTION_REVIEW.md](PRODUCTION_REVIEW.md) 和
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)。试听和搜索实测见
[PERFORMANCE_AND_BUILD_REVIEW.md](PERFORMANCE_AND_BUILD_REVIEW.md)。

## macOS 打包

```bash
make package-dev
```

产物：

```text
dist/SoundCloudMusic/
dist/SoundCloudMusic-macOS-arm64.zip
```

正式签名构建：

```bash
export CODESIGN_IDENTITY="Developer ID Application: ..."
make package
```
