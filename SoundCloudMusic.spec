# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH).resolve()
datas = []
binaries = []
hiddenimports = [
    "music_app",
    "music_app.cli",
    "music_app.media",
    "music_app.player",
    "music_app.selftest",
    "music_app.ui",
    "tkinter",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.ttk",
]

for package in (
    "yt_dlp",
    "pygame",
    "sounddevice",
    "imageio_ffmpeg",
    "mutagen",
    "certifi",
    "curl_cffi",
    "brotli",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

excludes = [
    "IPython",
    "jupyter",
    "mypy",
    "notebook",
    "pytest",
    "ruff",
    "sphinx",
    "unittest",
]


def analysis(entrypoint):
    return Analysis(
        [str(ROOT / entrypoint)],
        pathex=[str(ROOT)],
        binaries=binaries,
        datas=datas,
        hiddenimports=hiddenimports,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=excludes,
        noarchive=False,
        optimize=1,
    )


gui_analysis = analysis("soundcloud_gui.py")
gui_pyz = PYZ(gui_analysis.pure)
gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="SoundCloudMusic",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

cli_analysis = analysis("soundcloud_downloader.py")
cli_pyz = PYZ(cli_analysis.pure)
cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="SoundCloudMusicCLI",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    gui_exe,
    cli_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SoundCloudMusic",
)
