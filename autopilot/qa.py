"""Pre-publish QA gate (FR-4): file size, streams, resolution, black frames, silence."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from autopilot.media_utils import probe, run

MIN_BYTES = 1_000_000


def check(path: Path, expected_size: tuple[int, int]) -> dict[str, Any]:
    issues: list[str] = []
    report: dict[str, Any] = {"path": str(path)}
    size = path.stat().st_size if path.exists() else 0
    report["bytes"] = size
    if size <= MIN_BYTES:
        issues.append(f"file is {size} bytes (must be > 1 MB)")
        return {**report, "passed": False, "issues": issues}

    info = probe(path)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = float(info.get("format", {}).get("duration") or 0)
    report["duration"] = round(dur, 2)
    if v is None:
        issues.append("no video stream")
    else:
        report["resolution"] = f"{v.get('width')}x{v.get('height')}"
        if (int(v.get("width", 0)), int(v.get("height", 0))) != expected_size:
            issues.append(f"resolution {report['resolution']} != {expected_size[0]}x{expected_size[1]}")
    if a is None:
        issues.append("no audio stream")

    # Black frames: total black time and any single long black stretch.
    out = run(["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "blackdetect=d=0.3:pix_th=0.08",
               "-an", "-f", "null", "-"], timeout=900).stderr
    blacks = [float(x) for x in re.findall(r"black_duration:([\d.]+)", out)]
    report["black_seconds"] = round(sum(blacks), 2)
    if dur and (sum(blacks) > 0.15 * dur or any(b > 2.0 for b in blacks)):
        issues.append(f"{report['black_seconds']}s of black frames")

    if a is not None:
        out = run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"],
                  timeout=900).stderr
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", out)
        mean = float(m.group(1)) if m else -99.0
        report["mean_volume_db"] = mean
        if mean < -45:
            issues.append(f"audio is near-silent (mean {mean} dB)")

    return {**report, "passed": not issues, "issues": issues}
