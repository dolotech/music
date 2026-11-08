from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Protocol

from .catalog import SearchService, YtDlpSearchBackend
from .config import AppConfig, ConfigStore
from .downloads import DownloadProgress, DownloadService, DownloadSettings
from .errors import TaskCancelled, map_exception, user_error_text
from .json_logging import configure_logging
from .media import PreviewCache, PreviewService
from .models import DownloadRecord, TrackCandidate
from .player import AudioPlayer, PlayerSnapshot, PygameBackend
from .security import CancellationToken
from .storage import DownloadRepository, DownloadStaging

PROJECT_DIR = Path(__file__).resolve().parent.parent
WORK_DIR = PROJECT_DIR / "work"
DEFAULT_DOWNLOAD_DIR = PROJECT_DIR / "download"
SORT_OPTIONS = ("综合排行", "匹配度优先", "点赞数优先")
SORT_MODES = {
    "综合排行": "combined",
    "匹配度优先": "match",
    "点赞数优先": "likes",
}
SHORT_TRACK_SECONDS = 35


class DownloaderProtocol(Protocol):
    def download(
        self,
        track: TrackCandidate,
        token: CancellationToken,
        on_progress: Callable[[DownloadProgress], None],
        *,
        rights_acknowledged: bool,
    ) -> DownloadRecord: ...


class PreviewProtocol(Protocol):
    def prepare(
        self,
        track: TrackCandidate,
        token: CancellationToken,
        *,
        preserve: set[str] | None = None,
    ) -> Path: ...


class PlayerProtocol(Protocol):
    def load(self, path: Path, duration: float, autoplay: bool = True) -> None: ...
    def toggle(self) -> None: ...
    def seek(self, position: float) -> None: ...
    def stop(self) -> None: ...
    def release(self) -> None: ...
    def tick(self) -> PlayerSnapshot: ...


@dataclass(slots=True)
class AppServices:
    config: AppConfig
    config_store: ConfigStore
    search_service: SearchService
    download_service: DownloaderProtocol
    preview_service: PreviewProtocol
    player: PlayerProtocol | None
    player_backend: object | None
    logger: logging.Logger


def create_default_services() -> AppServices:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    logger = configure_logging(WORK_DIR / "logs" / "app.jsonl")
    config_store = ConfigStore(WORK_DIR / "config.json")
    config = config_store.load(PROJECT_DIR)

    download_dir = Path(config.download_dir)
    if not download_dir.is_absolute():
        download_dir = PROJECT_DIR / download_dir
    repository = DownloadRepository(download_dir / ".music_index.sqlite3")
    staging = DownloadStaging(download_dir, repository)
    download_service = DownloadService(
        staging,
        settings=DownloadSettings(),
        logger=logger,
    )
    preview_cache = PreviewCache(
        WORK_DIR / "preview_cache",
        config.preview_cache_mb * 1024 * 1024,
    )
    preview_service = PreviewService(preview_cache)

    backend = None
    player = None
    try:
        backend = PygameBackend()
        player = AudioPlayer(backend)
    except Exception:
        logger.exception("audio backend initialization failed")

    return AppServices(
        config=config,
        config_store=config_store,
        search_service=SearchService(YtDlpSearchBackend()),
        download_service=download_service,
        preview_service=preview_service,
        player=player,
        player_backend=backend,
        logger=logger,
    )


class SoundCloudApp:
    def __init__(
        self,
        root: tk.Tk,
        *,
        services: AppServices | None = None,
        max_parallel_downloads: int | None = None,
    ) -> None:
        self.root = root
        self.services = services or create_default_services()
        self.config = self.services.config
        self.logger = self.services.logger
        self.max_parallel = max_parallel_downloads or self.config.max_parallel_downloads
        self.events: queue.Queue[tuple] = queue.Queue()
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_parallel,
            thread_name_prefix="soundcloud-download",
        )
        self.download_tokens: dict[str, CancellationToken] = {}
        self.job_states: dict[str, str] = {}
        self.job_progress: dict[str, float] = {}
        self.job_errors: dict[str, str] = {}
        self.current_candidates: list[TrackCandidate] = []
        self.candidates: dict[str, TrackCandidate] = {}
        self.search_token: CancellationToken | None = None
        self.preview_token: CancellationToken | None = None
        self.preview_generation = 0
        self.preview_loading = False
        self.preview_seeking = False
        self.updating_player_scale = False

        self.download_dir = self._resolve_download_dir()
        self._apply_window_geometry()
        self.root.configure(bg="#f4f5f7")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.search_var = tk.StringVar()
        self.sort_var = tk.StringVar(value=SORT_OPTIONS[0])
        self.hide_short_var = tk.BooleanVar(
            value=self.config.hide_short_previews
        )
        self.rights_var = tk.BooleanVar(value=False)
        self.selected_title = tk.StringVar(value="尚未选择歌曲")
        self.selected_meta = tk.StringVar(value="")
        self.selected_url = tk.StringVar(value="")
        self.download_dir_var = tk.StringVar(value=str(self.download_dir))
        self.status_var = tk.StringVar(
            value="仅下载你有权保存的内容。"
        )
        self.progress_var = tk.DoubleVar(value=0.0)
        self.player_position_var = tk.DoubleVar(value=0.0)
        self.player_time_var = tk.StringVar(value="00:00 / 00:00")

        self._configure_style()
        self._build_ui()
        self.root.after(100, self._poll_events)
        self.search_entry.focus_set()

    def _apply_window_geometry(self) -> None:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(1180, max(880, screen_width - 120))
        height = min(920, max(700, screen_height - 140))
        left = max(0, (screen_width - width) // 2)
        top = max(24, (screen_height - height) // 2 - 20)
        self.root.geometry(f"{width}x{height}+{left}+{top}")
        self.root.minsize(min(980, width), min(860, height))

    def _resolve_download_dir(self) -> Path:
        path = Path(self.config.download_dir)
        return path if path.is_absolute() else PROJECT_DIR / path

    def _font(self, size: int, weight: str = "normal") -> tuple:
        families = set(tkfont.families(self.root))
        candidates = (
            "PingFang SC",
            "Microsoft YaHei UI",
            "Noto Sans CJK SC",
            "Helvetica",
        )
        family = next(
            (name for name in candidates if name in families),
            "TkDefaultFont",
        )
        return family, size, weight

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(
            "Search.TEntry",
            padding=(12, 9),
            fieldbackground="#ffffff",
            foreground="#1f2023",
        )
        style.configure(
            "Accent.TButton",
            background="#ff5500",
            foreground="#ffffff",
            borderwidth=0,
            padding=(15, 9),
            font=self._font(11, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#e94e00"), ("disabled", "#f2a983")],
        )
        style.configure(
            "Secondary.TButton",
            background="#ffffff",
            foreground="#35363a",
            padding=(10, 7),
        )
        style.configure(
            "Results.Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#26272b",
            rowheight=32,
            borderwidth=0,
            font=self._font(10),
        )
        style.configure(
            "Results.Treeview.Heading",
            background="#eceef1",
            foreground="#5d6067",
            padding=(7, 8),
            font=self._font(10, "bold"),
        )
        style.map(
            "Results.Treeview",
            background=[("selected", "#ffe6d9")],
            foreground=[("selected", "#1f2023")],
        )
        style.configure(
            "Download.Horizontal.TProgressbar",
            background="#ff5500",
            troughcolor="#e6e7e9",
            borderwidth=0,
        )

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg="#202124", height=104)
        header.pack(fill="x")
        header.pack_propagate(False)
        header_inner = tk.Frame(header, bg="#202124")
        header_inner.pack(fill="both", expand=True, padx=26, pady=15)
        tk.Label(
            header_inner,
            text="S",
            bg="#ff5500",
            fg="white",
            width=3,
            font=self._font(18, "bold"),
        ).pack(side="left", padx=(0, 12))
        heading = tk.Frame(header_inner, bg="#202124")
        heading.pack(side="left", fill="y")
        tk.Label(
            heading,
            text="SoundCloud 音乐搜索下载器",
            bg="#202124",
            fg="#ffffff",
            font=self._font(20, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            heading,
            text="授权检索 · 整首试听 · 并发下载 · DRM 识别",
            bg="#202124",
            fg="#aeb0b5",
            font=self._font(10),
            anchor="w",
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(self.root, bg="#f4f5f7")
        body.pack(fill="both", expand=True, padx=22, pady=12)
        search_row = tk.Frame(body, bg="#f4f5f7")
        search_row.pack(fill="x")
        self.search_entry = ttk.Entry(
            search_row,
            textvariable=self.search_var,
            style="Search.TEntry",
            font=self._font(13),
        )
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<Return>", lambda _event: self.start_search())
        self.search_button = ttk.Button(
            search_row,
            text="搜索",
            style="Accent.TButton",
            command=self.start_search,
        )
        self.search_button.pack(side="right", padx=(8, 0))
        sort_frame = tk.Frame(search_row, bg="#f4f5f7")
        sort_frame.pack(side="right", padx=(8, 0))
        tk.Label(
            sort_frame,
            text="排序",
            bg="#f4f5f7",
            fg="#55585e",
            font=self._font(10),
        ).pack(side="left", padx=(0, 5))
        self.sort_combo = ttk.Combobox(
            sort_frame,
            textvariable=self.sort_var,
            values=SORT_OPTIONS,
            width=10,
            state="readonly",
            font=self._font(10),
        )
        self.sort_combo.pack(side="left")
        self.sort_combo.bind("<<ComboboxSelected>>", self._on_sort_change)
        self.short_filter = ttk.Checkbutton(
            search_row,
            text="隐藏短预览",
            variable=self.hide_short_var,
            command=self._on_preview_filter_change,
        )
        self.short_filter.pack(side="right", padx=(8, 0))

        table_frame = tk.Frame(
            body,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#dddde1",
        )
        table_frame.pack(fill="both", expand=True, pady=(12, 10))
        columns = ("title", "artist", "duration", "likes", "score", "status")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
            style="Results.Treeview",
        )
        headings = {
            "title": "歌曲",
            "artist": "上传者 / 歌手",
            "duration": "时长",
            "likes": "点赞",
            "score": "匹配度",
            "status": "状态",
        }
        widths = {
            "title": 370,
            "artist": 185,
            "duration": 72,
            "likes": 78,
            "score": 78,
            "status": 105,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                minwidth=60,
                anchor="w" if column in {"title", "artist"} else "center",
            )
        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.tree.yview,
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_selection)
        self.tree.bind("<Double-1>", self._on_double_click)

        details = tk.Frame(
            body,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#dddde1",
        )
        details.pack(fill="x", pady=(0, 10))
        details_left = tk.Frame(details, bg="#ffffff")
        details_left.pack(side="left", fill="both", expand=True, padx=14, pady=10)
        tk.Label(
            details_left,
            textvariable=self.selected_title,
            bg="#ffffff",
            fg="#202124",
            font=self._font(13, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            details_left,
            textvariable=self.selected_meta,
            bg="#ffffff",
            fg="#6a6d73",
            font=self._font(9),
            anchor="w",
        ).pack(fill="x", pady=(3, 0))
        self.page_link = tk.Label(
            details_left,
            text="",
            bg="#ffffff",
            fg="#e64c00",
            cursor="hand2",
            font=self._font(8),
            anchor="w",
        )
        self.page_link.pack(fill="x", pady=(4, 0))
        self.page_link.bind("<Button-1>", self._open_selected_page)

        actions = tk.Frame(details, bg="#ffffff")
        actions.pack(side="right", padx=12, pady=10)
        self.preview_button = ttk.Button(
            actions,
            text="试听整首",
            style="Secondary.TButton",
            command=self.start_preview,
            state="disabled",
        )
        self.preview_button.pack(side="left", padx=(0, 5))
        self.download_button = ttk.Button(
            actions,
            text="下载 M4A",
            style="Accent.TButton",
            command=self.start_download,
            state="disabled",
        )
        self.download_button.pack(side="left")
        self.rights_check = ttk.Checkbutton(
            actions,
            text="我确认有权下载",
            variable=self.rights_var,
            command=self._update_action_state,
        )
        self.rights_check.pack(side="left", padx=(8, 0))

        player = tk.Frame(
            body,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#dddde1",
        )
        player.pack(fill="x", pady=(0, 10))
        player_controls = tk.Frame(player, bg="#ffffff")
        player_controls.pack(fill="x", padx=12, pady=8)
        tk.Label(
            player_controls,
            text="播放器",
            bg="#ffffff",
            fg="#202124",
            font=self._font(10, "bold"),
        ).pack(side="left", padx=(0, 8))
        self.play_button = ttk.Button(
            player_controls,
            text="播放",
            style="Secondary.TButton",
            command=self.toggle_playback,
            state="disabled",
        )
        self.play_button.pack(side="left")
        self.stop_button = ttk.Button(
            player_controls,
            text="停止",
            style="Secondary.TButton",
            command=self.stop_playback,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=(5, 10))
        self.player_scale = ttk.Scale(
            player_controls,
            from_=0,
            to=1,
            variable=self.player_position_var,
            command=self._on_player_scale_move,
        )
        self.player_scale.pack(side="left", fill="x", expand=True)
        self.player_scale.bind("<ButtonPress-1>", self._on_seek_start)
        self.player_scale.bind("<ButtonRelease-1>", self._on_seek_end)
        tk.Label(
            player_controls,
            textvariable=self.player_time_var,
            bg="#ffffff",
            fg="#55585e",
            width=14,
            anchor="e",
            font=self._font(9),
        ).pack(side="right", padx=(8, 0))

        footer = tk.Frame(body, bg="#f4f5f7")
        footer.pack(fill="x")
        folder_row = tk.Frame(footer, bg="#f4f5f7")
        folder_row.pack(fill="x")
        tk.Label(
            folder_row,
            text="保存到：",
            bg="#f4f5f7",
            fg="#55585e",
            font=self._font(9),
        ).pack(side="left")
        tk.Label(
            folder_row,
            textvariable=self.download_dir_var,
            bg="#f4f5f7",
            fg="#55585e",
            font=self._font(9),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self.change_dir_button = ttk.Button(
            folder_row,
            text="更改目录",
            style="Secondary.TButton",
            command=self.choose_directory,
        )
        self.change_dir_button.pack(side="right")
        self.open_dir_button = ttk.Button(
            folder_row,
            text="打开目录",
            style="Secondary.TButton",
            command=self.open_download_directory,
        )
        self.open_dir_button.pack(side="right", padx=(0, 6))
        self.cancel_button = ttk.Button(
            folder_row,
            text="取消所选下载",
            style="Secondary.TButton",
            command=self.cancel_selected_downloads,
        )
        self.cancel_button.pack(side="right", padx=(0, 6))

        self.progress = ttk.Progressbar(
            footer,
            variable=self.progress_var,
            maximum=100,
            style="Download.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x", pady=(8, 5))
        tk.Label(
            footer,
            textvariable=self.status_var,
            bg="#f4f5f7",
            fg="#5d6067",
            anchor="w",
            justify="left",
            font=self._font(9),
        ).pack(fill="x")
        tk.Label(
            footer,
            text="Command/Ctrl 多选 · 双击单首试听 · 点击下载按钮才保存文件",
            bg="#f4f5f7",
            fg="#96999f",
            anchor="e",
            font=self._font(8),
        ).pack(fill="x", pady=(2, 0))

    def _emit(self, kind: str, *payload: object) -> None:
        self.events.put((kind, *payload))

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self._update_player_display()
        if self.root.winfo_exists():
            self.root.after(100, self._poll_events)

    def _handle_event(self, event: tuple) -> None:
        kind, *payload = event
        if kind == "search_done":
            generation, query, candidates = payload
            self._show_search_results(generation, query, candidates)
        elif kind == "search_error":
            generation, error = payload
            if generation == self._current_search_generation():
                self.search_button.configure(state="normal")
                self.status_var.set(user_error_text(error))
                mapped = map_exception(error)
                self.logger.error(
                    "search failed",
                    extra={
                        "event": "search_failed",
                        "error_code": mapped.code,
                    },
                )
        elif kind == "download_started":
            self.job_states[payload[0]] = "downloading"
            self._refresh_row(payload[0])
        elif kind == "download_progress":
            track_id, progress = payload
            self.job_states[track_id] = "downloading"
            self.job_progress[track_id] = progress.percent
            self._refresh_row(track_id)
            self._update_overall_progress()
            track = self.candidates.get(track_id)
            if track:
                self.status_var.set(f"{track.title}：{progress.message}")
        elif kind == "download_done":
            track_id, track, record = payload
            self.job_states[track_id] = "done"
            self.job_progress[track_id] = 100.0
            self.download_tokens.pop(track_id, None)
            self._refresh_row(track_id)
            self._update_overall_progress()
            self.status_var.set(f"下载完成：{Path(record.path).name}")
            self.logger.info(
                "download completed",
                extra={"event": "download_completed", "track_id": track_id},
            )
        elif kind == "download_error":
            track_id, track, error = payload
            self._download_failed(track_id, track, error)
        elif kind == "download_cancelled":
            track_id, track = payload
            self.job_states[track_id] = "cancelled"
            self.job_progress.pop(track_id, None)
            self.download_tokens.pop(track_id, None)
            self._refresh_row(track_id)
            self._update_overall_progress()
            self.status_var.set(f"已取消：{track.title}")
            self.logger.info(
                "download cancelled",
                extra={"event": "download_cancelled", "track_id": track_id},
            )
        elif kind == "preview_ready":
            generation, track, path = payload
            self._preview_ready(generation, track, path)
        elif kind == "preview_error":
            generation, error = payload
            if generation == self.preview_generation:
                self.preview_loading = False
                self.preview_token = None
                self._update_action_state()
                self.stop_button.configure(state="disabled")
                self.status_var.set("试听准备失败")
                mapped = map_exception(error)
                self.logger.error(
                    "preview failed",
                    extra={
                        "event": "preview_failed",
                        "error_code": mapped.code,
                    },
                )
                messagebox.showerror("试听失败", user_error_text(error))
        elif kind == "log":
            level, message = payload
            if level == "error":
                self.logger.error(
                    str(message),
                    extra={"event": "ytdlp_error"},
                )

    def _current_search_generation(self) -> int:
        return getattr(self, "_search_generation", 0)

    def start_search(self) -> None:
        query = self.search_var.get().strip()
        if not query:
            messagebox.showinfo("请输入歌名", "请输入完整的歌名或部分关键词。")
            self.search_entry.focus_set()
            return
        if self.search_token is not None:
            self.search_token.cancel("已开始新的搜索")
        token = CancellationToken()
        self.search_token = token
        self._search_generation = self._current_search_generation() + 1
        generation = self._search_generation
        self.search_button.configure(state="disabled")
        self.status_var.set(f"正在搜索“{query}”...")
        self._focus_search_entry()

        def worker() -> None:
            try:
                results = self.services.search_service.search(
                    query,
                    self.config.search_limit,
                    token,
                )
                self._emit("search_done", generation, query, results)
            except TaskCancelled:
                return
            except Exception as exc:
                self._emit("search_error", generation, exc)

        threading.Thread(target=worker, daemon=True).start()

    def _show_search_results(
        self,
        generation: int,
        query: str,
        candidates: list[TrackCandidate],
    ) -> None:
        if generation != self._current_search_generation():
            return
        self.search_button.configure(state="normal")
        self._focus_search_entry()
        self.current_candidates = candidates
        self._populate_results()
        if candidates:
            visible = len(self._visible_candidates())
            hidden = len(candidates) - visible
            extra = f"，已隐藏 {hidden} 个短预览" if hidden else ""
            self.status_var.set(f"找到 {visible} 个候选{extra}。")
        else:
            self._clear_selection()
            self.status_var.set(f"没有找到与“{query}”匹配的歌曲。")

    def _focus_search_entry(self) -> None:
        self.search_entry.focus_set()
        self.search_entry.icursor("end")

    def _visible_candidates(self) -> list[TrackCandidate]:
        if not self.hide_short_var.get():
            return list(self.current_candidates)
        return [
            item
            for item in self.current_candidates
            if item.duration is None or item.duration > SHORT_TRACK_SECONDS
        ]

    def _sort_key(self, track: TrackCandidate) -> tuple[object, ...]:
        mode = SORT_MODES.get(self.sort_var.get(), "combined")
        if mode == "likes":
            return (-track.likes, -track.score, track.title.casefold())
        if mode == "match":
            return (-track.score, -track.likes, track.title.casefold())
        max_likes = max(
            (item.likes for item in self._visible_candidates()),
            default=0,
        )
        like_weight = track.likes / max_likes if max_likes else 0.0
        return (
            -(track.score * 0.75 + like_weight * 0.25),
            -track.score,
            -track.likes,
        )

    def _populate_results(self) -> None:
        selected = set(self.tree.selection())
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.candidates.clear()
        for candidate in sorted(self._visible_candidates(), key=self._sort_key):
            self.candidates[candidate.track_id] = candidate
            self.tree.insert(
                "",
                "end",
                iid=candidate.track_id,
                values=self._values(candidate),
            )
        restored = [iid for iid in selected if self.tree.exists(iid)]
        if restored:
            self.tree.selection_set(restored)
            self.tree.focus(restored[0])
            self._on_selection()
        elif self.tree.get_children():
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self._on_selection()
        else:
            self._clear_selection()

    def _state_text(self, track: TrackCandidate) -> str:
        if track.is_drm:
            return "DRM 受限"
        if track.downloadable is False:
            return "不可下载"
        state = self.job_states.get(track.track_id)
        if state == "queued":
            return "排队中"
        if state == "downloading":
            return f"下载 {self.job_progress.get(track.track_id, 0):.0f}%"
        if state == "done":
            return "完成"
        if state == "error":
            return "失败"
        if state == "drm":
            return "DRM 受限"
        if state == "cancelled":
            return "已取消"
        return ""

    def _values(self, track: TrackCandidate) -> tuple:
        return (
            track.title,
            track.artist,
            track.duration_text,
            track.likes_text,
            track.score_text,
            self._state_text(track),
        )

    def _refresh_row(self, track_id: str) -> None:
        track = self.candidates.get(track_id)
        if track and self.tree.exists(track_id):
            self.tree.item(track_id, values=self._values(track))

    def _on_sort_change(self, _event: tk.Event | None = None) -> None:
        self._populate_results()

    def _on_preview_filter_change(self) -> None:
        self.config.hide_short_previews = self.hide_short_var.get()
        self.services.config_store.save(self.config)
        self._populate_results()
        if self.current_candidates and not self._visible_candidates():
            self.status_var.set(
                "搜索结果只有短预览；取消“隐藏短预览”可显示。"
            )

    def _selected_tracks(self) -> list[TrackCandidate]:
        return [
            track
            for iid in self.tree.selection()
            if (track := self.candidates.get(iid)) is not None
        ]

    def _on_selection(self, _event: tk.Event | None = None) -> None:
        selected = self.tree.selection()
        if not selected:
            self._clear_selection()
            return
        track = self.candidates.get(selected[0])
        if not track:
            return
        self.selected_title.set(track.title)
        self.selected_meta.set(
            f"{track.artist} · {track.duration_text} · "
            f"点赞 {track.likes_text} · 匹配 {track.score_text}"
        )
        self.selected_url.set(track.webpage_url)
        self.page_link.configure(text=track.webpage_url)
        self._update_action_state()

    def _update_action_state(self) -> None:
        tracks = self._selected_tracks()
        can_preview = (
            bool(tracks)
            and self.services.player is not None
        )
        can_download = (
            bool(tracks)
            and self.rights_var.get()
            and all(
                track.downloadable is not False and not track.is_drm
                for track in tracks
            )
        )
        self.preview_button.configure(
            state="normal" if can_preview else "disabled",
            text="切换试听" if self.preview_loading else "试听整首",
        )
        self.download_button.configure(
            state="normal" if can_download else "disabled",
            text=(
                f"下载选中 ({len(tracks)})"
                if len(tracks) > 1
                else "下载 M4A"
            ),
        )

    def _clear_selection(self) -> None:
        self.selected_title.set("尚未选择歌曲")
        self.selected_meta.set("")
        self.selected_url.set("")
        self.page_link.configure(text="")
        self.preview_button.configure(state="disabled")
        self.download_button.configure(state="disabled")

    def _on_double_click(self, event: tk.Event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self._on_selection()
        self.start_preview()

    def _open_selected_page(self, _event: tk.Event | None = None) -> None:
        if self.selected_url.get():
            webbrowser.open(self.selected_url.get())

    def choose_directory(self) -> None:
        selected = filedialog.askdirectory(
            title="选择音乐保存目录",
            initialdir=str(self.download_dir),
        )
        if not selected:
            return
        self.download_dir = Path(selected)
        self.download_dir_var.set(selected)
        self.config.download_dir = selected
        self.services.config_store.save(self.config)
        self._rebuild_download_service()

    def _rebuild_download_service(self) -> None:
        repository = DownloadRepository(
            self.download_dir / ".music_index.sqlite3"
        )
        self.services.download_service = DownloadService(
            DownloadStaging(self.download_dir, repository),
            settings=DownloadSettings(),
            logger=self.logger,
        )

    def open_download_directory(self) -> None:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(self.download_dir)])
        elif os.name == "nt":
            os.startfile(self.download_dir)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(self.download_dir)])

    def start_download(self) -> None:
        tracks = self._selected_tracks()
        if not tracks:
            messagebox.showinfo("请选择歌曲", "请先选择歌曲。")
            return
        if not self.rights_var.get():
            messagebox.showinfo(
                "需要确认授权",
                "请先勾选“我确认有权下载”。",
            )
            return
        short = [
            item
            for item in tracks
            if item.duration is not None
            and item.duration <= SHORT_TRACK_SECONDS
        ]
        if short:
            names = "\n".join(
                f"• {item.title}（{item.duration_text}）"
                for item in short[:4]
            )
            if not messagebox.askyesno(
                "短预览确认",
                f"以下候选可能只是短预览：\n\n{names}\n\n仍要下载吗？",
            ):
                return

        queued = 0
        for track in tracks:
            if track.track_id in self.download_tokens:
                continue
            token = CancellationToken()
            self.download_tokens[track.track_id] = token
            self.job_states[track.track_id] = "queued"
            self.job_progress[track.track_id] = 0.0
            self.job_errors.pop(track.track_id, None)
            self._refresh_row(track.track_id)
            self.executor.submit(self._download_worker, track, token)
            queued += 1
        if queued:
            self.status_var.set(
                f"已加入 {queued} 个任务，最多并发 {self.max_parallel}。"
            )
            self._update_overall_progress()

    def _download_worker(
        self,
        track: TrackCandidate,
        token: CancellationToken,
    ) -> None:
        self._emit("download_started", track.track_id)
        try:
            record = self.services.download_service.download(
                track,
                token,
                lambda progress: self._emit(
                    "download_progress",
                    track.track_id,
                    progress,
                ),
                rights_acknowledged=True,
            )
            self._emit("download_done", track.track_id, track, record)
        except TaskCancelled:
            self._emit("download_cancelled", track.track_id, track)
        except Exception as exc:
            self._emit("download_error", track.track_id, track, exc)

    def cancel_selected_downloads(self) -> None:
        tracks = self._selected_tracks()
        cancelled = 0
        for track in tracks:
            token = self.download_tokens.get(track.track_id)
            if token:
                token.cancel("用户取消下载")
                cancelled += 1
        if not cancelled:
            self.status_var.set("所选歌曲没有正在下载或排队的任务。")

    def _download_failed(
        self,
        track_id: str,
        track: TrackCandidate,
        exc: BaseException,
    ) -> None:
        error = map_exception(exc)
        self.job_states[track_id] = (
            "drm" if error.code == "drm_protected" else "error"
        )
        self.job_progress.pop(track_id, None)
        self.download_tokens.pop(track_id, None)
        text = user_error_text(error)
        self.job_errors[track_id] = f"{track.title}：{text}"
        self._refresh_row(track_id)
        self._update_overall_progress()
        self.status_var.set(f"下载失败：{track.title}")
        self.logger.error(
            "download failed",
            extra={
                "event": "download_failed",
                "track_id": track_id,
                "error_code": error.code,
            },
        )
        self._show_errors_if_finished()

    def _active_job_progress(self) -> list[float]:
        return [
            self.job_progress.get(track_id, 0.0)
            for track_id, state in self.job_states.items()
            if state in {"queued", "downloading"}
        ]

    def _update_overall_progress(self) -> None:
        active = self._active_job_progress()
        if active:
            self.progress_var.set(sum(active) / len(active))
        elif any(state == "done" for state in self.job_states.values()):
            self.progress_var.set(100)
        else:
            self.progress_var.set(0)

    def _show_errors_if_finished(self) -> None:
        if self._active_job_progress() or not self.job_errors:
            return
        errors = list(self.job_errors.values())
        self.job_errors.clear()
        messagebox.showerror("下载失败", "\n\n".join(errors[:3]))

    def start_preview(self) -> None:
        if self.services.player is None:
            messagebox.showerror(
                "播放器不可用",
                "音频播放器初始化失败，请重新启动或安装依赖。",
            )
            return
        tracks = self._selected_tracks()
        if not tracks:
            return
        track = tracks[0]
        if self.preview_token is not None:
            self.preview_token.cancel("已切换试听")
        self.preview_token = CancellationToken()
        token = self.preview_token
        self.preview_generation += 1
        generation = self.preview_generation
        self.preview_loading = True
        self._update_action_state()
        self.services.player.release()
        self.play_button.configure(state="disabled", text="播放")
        self.stop_button.configure(state="normal")
        self.status_var.set(f"正在加载整首试听：{track.title}")
        preserve_tracks = set(self.tree.selection())

        def worker() -> None:
            try:
                path = self.services.preview_service.prepare(
                    track,
                    token,
                    preserve=preserve_tracks,
                )
                self._emit("preview_ready", generation, track, path)
            except TaskCancelled:
                return
            except Exception as exc:
                self._emit("preview_error", generation, exc)

        threading.Thread(target=worker, daemon=True).start()

    def _preview_ready(
        self,
        generation: int,
        track: TrackCandidate,
        path: Path,
    ) -> None:
        if generation != self.preview_generation:
            return
        self.preview_loading = False
        self.preview_token = None
        player = self.services.player
        if player is None:
            return
        try:
            player.load(path, track.duration or 0)
        except Exception as exc:
            self._update_action_state()
            messagebox.showerror("试听失败", user_error_text(exc))
            return
        self.player_scale.configure(to=max(1.0, track.duration or 1.0))
        self.play_button.configure(state="normal", text="暂停")
        self.stop_button.configure(state="normal")
        self.status_var.set(f"正在试听整首：{track.title}")
        self._update_action_state()

    def toggle_playback(self) -> None:
        player = self.services.player
        if player is None:
            return
        try:
            player.toggle()
        except Exception as exc:
            messagebox.showerror("播放失败", user_error_text(exc))
            return
        snapshot = player.tick()
        self.play_button.configure(text="暂停" if snapshot.playing else "继续")

    def stop_playback(self) -> None:
        if self.preview_token is not None:
            self.preview_token.cancel("用户停止试听")
            self.preview_token = None
        self.preview_generation += 1
        self.preview_loading = False
        player = self.services.player
        if player is not None:
            player.release()
        self.play_button.configure(state="disabled", text="播放")
        self.stop_button.configure(state="disabled")
        self._set_player_display(0.0, 0.0)
        self.status_var.set("试听已停止")
        self._update_action_state()

    def _format_time(self, seconds: float) -> str:
        total = max(0, round(seconds))
        minutes, secs = divmod(total, 60)
        return f"{minutes:02d}:{secs:02d}"

    def _set_player_display(self, position: float, duration: float) -> None:
        self.updating_player_scale = True
        self.player_position_var.set(position)
        self.updating_player_scale = False
        self.player_time_var.set(
            f"{self._format_time(position)} / {self._format_time(duration)}"
        )

    def _on_player_scale_move(self, value: str) -> None:
        player = self.services.player
        if self.updating_player_scale or player is None:
            return
        snapshot = player.tick()
        self.player_time_var.set(
            f"{self._format_time(float(value))} / "
            f"{self._format_time(snapshot.duration)}"
        )

    def _on_seek_start(self, _event: tk.Event | None = None) -> None:
        self.preview_seeking = True

    def _on_seek_end(self, _event: tk.Event | None = None) -> None:
        player = self.services.player
        if player is None:
            return
        self.preview_seeking = False
        player.seek(float(self.player_position_var.get()))

    def _update_player_display(self) -> None:
        player = self.services.player
        if player is None or self.preview_seeking:
            return
        snapshot = player.tick()
        if not snapshot.loaded:
            return
        self._set_player_display(snapshot.position, snapshot.duration)
        if snapshot.finished:
            self.play_button.configure(text="重新播放")
            self.status_var.set("试听结束")

    def close(self) -> None:
        for token in self.download_tokens.values():
            token.cancel("程序关闭")
        if self.preview_token is not None:
            self.preview_token.cancel("程序关闭")
        if self.services.player is not None:
            self.services.player.release()
        backend = self.services.player_backend
        if backend is not None and hasattr(backend, "close"):
            with suppress(Exception):
                backend.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.config.download_dir = str(self.download_dir)
        self.config.hide_short_previews = self.hide_short_var.get()
        try:
            self.services.config_store.save(self.config)
        except OSError:
            self.logger.exception("failed to save config")
        self.root.destroy()


def main() -> int:
    import tkinter as tk

    root = tk.Tk()
    SoundCloudApp(root)
    root.mainloop()
    return 0
