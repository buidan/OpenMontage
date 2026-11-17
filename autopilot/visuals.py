"""Stock visual sourcing for each scene (Pexels video -> Pexels/Wikimedia stills).

Scenes longer than one shot are split into several shots so fast-paced
short-form edits never hold one clip too long, and so a clip is never asked
to play past its own end (which would leave black frames).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from autopilot.media_utils import run
from tools.video.stock_sources.base import Candidate, SearchFilters

log = logging.getLogger("autopilot.visuals")

KEN_BURNS = ["ken-burns", "zoom-in", "pan-left", "zoom-out", "pan-right"]


@dataclass
class Shot:
    id: str
    scene_id: str
    start: float
    end: float
    kind: str                 # video | image | text
    path: Optional[str] = None
    text: str = ""
    animation: str = ""
    credit: dict[str, Any] | None = None


def plan_shot_lengths(scene_seconds: float, max_shot: float) -> list[float]:
    n = max(1, int(-(-scene_seconds // max_shot)))  # ceil
    return [scene_seconds / n] * n


class VisualSourcer:
    def __init__(self, out_dir: Path, orientation: str, sources: Optional[dict[str, Any]] = None) -> None:
        self.out_dir = out_dir
        self.orientation = orientation  # portrait | landscape
        self.used: set[str] = set()
        self._cache: dict[tuple[str, str, str], list[Candidate]] = {}
        if sources is None:
            from tools.video.stock_sources.pexels import PexelsSource
            from tools.video.stock_sources.wikimedia import WikimediaSource
            sources = {"pexels": PexelsSource(), "wikimedia": WikimediaSource()}
        self.sources = sources

    # -- search ---------------------------------------------------------
    def _search(self, source_name: str, query: str, kind: str) -> list[Candidate]:
        key = (source_name, query.lower(), kind)
        if key in self._cache:
            return self._cache[key]
        src = self.sources.get(source_name)
        results: list[Candidate] = []
        if src is not None and src.is_available():
            filters = SearchFilters(kind=kind, orientation=self.orientation, per_page=15,
                                    min_width=720 if kind == "video" else 800)
            try:
                results = src.search(query, filters)
            except Exception as exc:  # one flaky API must not kill the run
                log.warning("%s search failed for %r: %s", source_name, query, exc)
        self._cache[key] = results
        return results

    def _orientation_ok(self, c: Candidate) -> bool:
        if not c.width or not c.height:
            return True
        return (c.height >= c.width) if self.orientation == "portrait" else (c.width >= c.height)

    def _pick(self, queries: list[str], min_seconds: float) -> Optional[Candidate]:
        for q in queries:
            for c in self._search("pexels", q, "video"):
                if c.clip_id not in self.used and c.duration >= min_seconds and self._orientation_ok(c):
                    return c
        for source_name in ("pexels", "wikimedia"):
            for q in queries:
                for c in self._search(source_name, q, "image"):
                    if c.clip_id in self.used:
                        continue
                    # Wikimedia has few portrait photos; Remotion crops with object-fit: cover.
                    if source_name == "pexels" and not self._orientation_ok(c):
                        continue
                    return c
        return None

    def _download(self, c: Candidate) -> Path:
        ext = ".mp4" if c.kind == "video" else (Path(c.download_url.split("?")[0]).suffix.lower() or ".jpg")
        if ext not in {".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".webp"}:
            ext = ".jpg"
        path = self.out_dir / ("video" if c.kind == "video" else "images") / f"{c.clip_id}{ext}"
        if c.kind == "video":
            if not path.exists() or path.stat().st_size == 0:
                self.sources[c.source].download(c, path)
            return path
        # Stills: archives (Wikimedia especially) serve originals up to ~200 MP, which the
        # headless browser re-decodes every frame. Normalise to <= 2400 px JPEG once.
        final = path.with_name(f"{c.clip_id}.jpg")
        if not final.exists() or final.stat().st_size == 0:
            raw = path.with_name(f"{c.clip_id}.orig{ext}")
            self.sources[c.source].download(c, raw)
            run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-frames:v", "1", "-vf",
                 "scale=w='min(iw,2400)':h='min(ih,2400)':force_original_aspect_ratio=decrease",
                 "-q:v", "3", str(final)])
            raw.unlink(missing_ok=True)
        return final

    # -- public ---------------------------------------------------------
    def shots_for_scene(self, scene, start: float, end: float, max_shot: float, shot_index: int) -> list[Shot]:
        shots: list[Shot] = []
        t = start
        queries = [q for q in dict.fromkeys([scene.visual_query, scene.fallback_query]) if q]
        for i, length in enumerate(plan_shot_lengths(end - start, max_shot)):
            shot_id = f"{scene.id}_{i + 1}"
            cand = self._pick(queries, min_seconds=length + 0.5)
            shot: Shot
            if cand is not None:
                try:
                    path = self._download(cand)
                    self.used.add(cand.clip_id)
                    shot = Shot(shot_id, scene.id, t, t + length, cand.kind, str(path),
                                animation="" if cand.kind == "video" else KEN_BURNS[(shot_index + i) % len(KEN_BURNS)],
                                credit={"source": cand.source, "id": cand.source_id, "url": cand.source_url,
                                        "creator": cand.creator, "license": cand.license})
                except Exception as exc:
                    log.warning("download failed for %s: %s", cand.clip_id, exc)
                    self.used.add(cand.clip_id)
                    cand = None
            if cand is None:
                shot = Shot(shot_id, scene.id, t, t + length, "text",
                            text=scene.on_screen_text or scene.visual_query.title())
            shots.append(shot)
            t += length
        return shots
