"""Thin ffprobe/ffmpeg helpers shared by the autopilot stages."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


class MediaError(RuntimeError):
    pass


def require_binaries(*names: str) -> None:
    missing = [n for n in names if not shutil.which(n)]
    if missing:
        raise MediaError(f"Missing required binaries on PATH: {', '.join(missing)}")


def run(cmd: list[str], timeout: float = 600) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[-15:])
        raise MediaError(f"{Path(cmd[0]).name} failed (exit {proc.returncode}):\n{tail}")
    return proc


def probe(path: str | Path) -> dict[str, Any]:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    return json.loads(out.stdout or "{}")


def duration(path: str | Path) -> float:
    return float(probe(path).get("format", {}).get("duration") or 0.0)
