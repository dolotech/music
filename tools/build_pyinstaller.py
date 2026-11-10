#!/usr/bin/env python3
"""Build the SoundCloud Music onedir package and run product self-checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "dist" / "SoundCloudMusic"
REPORT = ROOT / "dist" / "selftest-report.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def create_launcher(stage: Path) -> None:
    launcher = stage / "启动 SoundCloud Music.command"
    launcher.write_text(
        "#!/bin/bash\n"
        'cd "$(dirname "$0")"\n'
        'exec ./SoundCloudMusic\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    (stage / "使用说明.txt").write_text(
        "SoundCloud Music\n\n"
        "双击“启动 SoundCloud Music.command”运行图形界面。\n"
        "SoundCloudMusicCLI 提供命令行下载功能。\n"
        "数据与配置位于 ~/Library/Application Support/SoundCloudMusic。\n",
        encoding="utf-8",
    )


def run_selftest(stage: Path) -> dict:
    executable = stage / "SoundCloudMusic"
    env = os.environ.copy()
    env["SOUNDCLOUD_MUSIC_SELFTEST_REPORT"] = str(REPORT)
    REPORT.unlink(missing_ok=True)
    result = subprocess.run(
        [str(executable), "--selftest"],
        cwd=stage,
        env=env,
        timeout=60,
        check=False,
    )
    if result.returncode != 0 or not REPORT.is_file():
        raise SystemExit(f"packaged selftest failed with exit code {result.returncode}")
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if not report.get("ok"):
        raise SystemExit(f"packaged selftest failed: {report}")
    return report


def sign_app(stage: Path) -> None:
    identity = os.environ.get("CODESIGN_IDENTITY")
    if not identity:
        print("未设置 CODESIGN_IDENTITY，开发包保持未签名。")
        return
    paths = [
        stage / "SoundCloudMusic",
        stage / "SoundCloudMusicCLI",
    ]
    for path in paths:
        subprocess.run(
            [
                "/usr/bin/codesign",
                "--force",
                "--options",
                "runtime",
                "--timestamp",
                "--sign",
                identity,
                str(path),
            ],
            check=True,
        )


def verify_macho_dependencies(stage: Path) -> None:
    if sys.platform != "darwin":
        return
    forbidden_prefixes = (
        "/Library/Frameworks/Python",
        "/opt/homebrew",
        "/usr/local/lib",
    )
    for executable in (
        stage / "SoundCloudMusic",
        stage / "SoundCloudMusicCLI",
    ):
        result = subprocess.run(
            ["/usr/bin/otool", "-L", str(executable)],
            capture_output=True,
            text=True,
            check=True,
        )
        external = [
            line.strip()
            for line in result.stdout.splitlines()[1:]
            if line.strip().startswith(forbidden_prefixes)
        ]
        if external:
            raise SystemExit(
                f"external dependencies in {executable.name}: {external}"
            )


def archive(stage: Path) -> Path:
    output = ROOT / "dist" / "SoundCloudMusic-macOS-arm64.zip"
    output.unlink(missing_ok=True)
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as bundle:
        for path in sorted(stage.rglob("*")):
            bundle.write(
                path,
                Path("SoundCloudMusic") / path.relative_to(stage),
            )
    return output


def write_build_info(stage: Path) -> None:
    info = {
        "built_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "git": subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        or "unavailable",
    }
    (stage / "build-info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", nargs="?", default="SoundCloudMusic.spec")
    parser.add_argument("--release", action="store_true")
    args, pyinstaller_args = parser.parse_known_args()

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(ROOT / args.spec),
        *pyinstaller_args,
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    if not STAGE.is_dir():
        raise SystemExit(f"missing PyInstaller output: {STAGE}")

    create_launcher(STAGE)
    write_build_info(STAGE)
    verify_macho_dependencies(STAGE)
    if args.release:
        sign_app(STAGE)
        verify = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", "SoundCloudMusic"],
            cwd=STAGE,
            check=False,
        )
        if verify.returncode != 0:
            raise SystemExit("codesign verification failed")
    report = run_selftest(STAGE)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    output = archive(STAGE)
    print(f"artifact: {output}")
    print(f"sha256: {sha256(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
