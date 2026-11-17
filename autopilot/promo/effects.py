"""Gameplay-clip effects for the PRD promo templates (no drawtext/libass needed).

- ``sticker``: a kawaii pill / circle label rendered with Pillow in the app's palette and font
- ``retime``: rebuild a clip from segments: speed ramps (setpts + atempo) and freeze-frames
- ``overlay``: composite sticker PNGs onto a clip at given times (ffmpeg overlay + enable)
- ``split_layout``: gameplay in the lower part of the frame, a banner on top

Times passed to ``overlay`` are in the output clip's timeline; use ``TimeMap`` from
``retime`` to convert PromoBot event times (recorded on the original clip).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from autopilot.media_utils import probe, run

W, H = 1080, 1920


# --------------------------------------------------------------------------- stickers

def _font(font_path: Optional[str], size: int):
    from PIL import ImageFont
    if font_path and Path(font_path).is_file():
        f = ImageFont.truetype(font_path, size)
        try:  # variable fonts (Quicksand / Nunito): bold weight
            f.set_variation_by_axes([700])
        except Exception:
            pass
        return f
    return ImageFont.load_default(size=size)


def _rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha


def sticker(text: str, out: Path, *, font_path: Optional[str], fg: str = "#3B2F2F", bg: str = "#FFFFFF",
            accent: str = "#F08A7E", size: int = 72, circle: bool = False, max_width: int = 900) -> Path:
    """A clay-style label: rounded pill (or circle) with an accent rim and a soft shadow."""
    from PIL import Image, ImageDraw, ImageFilter

    font = _font(font_path, size)
    while font.getlength(text) > max_width - 120 and size > 36:
        size -= 4
        font = _font(font_path, size)
    tw = int(font.getlength(text))
    asc, desc = font.getmetrics()
    th = asc + desc
    if circle:
        d = max(tw, th) + 90
        bw, bh = d, d
    else:
        bw, bh = tw + 110, th + 60
    rim, shadow = 8, 14
    img = Image.new("RGBA", (bw + 2 * shadow, bh + 2 * shadow), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    box = (shadow, shadow + 6, shadow + bw, shadow + bh + 6)
    radius = bh // 2
    ImageDraw.Draw(sh).rounded_rectangle(box, radius=radius, fill=(60, 40, 40, 70))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(8)))
    draw = ImageDraw.Draw(img)
    body = (shadow, shadow, shadow + bw, shadow + bh)
    draw.rounded_rectangle(body, radius=radius, fill=_rgba(accent))
    inner = (body[0] + rim, body[1] + rim, body[2] - rim, body[3] - rim)
    draw.rounded_rectangle(inner, radius=radius - rim, fill=_rgba(bg))
    draw.text((shadow + bw / 2, shadow + bh / 2), text, font=font, fill=_rgba(fg), anchor="mm")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def banner(text: str, out: Path, *, height: int, font_path: Optional[str], bg: str, fg: str, accent: str) -> Path:
    """A full-width top banner (for split layouts) with a centered sticker on it."""
    from PIL import Image

    tag = sticker(text, out.with_suffix(".tag.png"), font_path=font_path, fg=fg, bg="#FFFFFF", accent=accent,
                  size=80, max_width=W - 80)
    canvas = Image.new("RGBA", (W, height), _rgba(bg))
    t = Image.open(tag)
    canvas.alpha_composite(t, ((W - t.width) // 2, max(220, (height - t.height) // 2)))
    canvas.save(out)
    return out


# --------------------------------------------------------------------------- retiming

@dataclass(frozen=True)
class Segment:
    start: float
    end: float = 0.0
    rate: float = 1.0      # >1 = faster
    freeze: float = 0.0    # >0: hold the frame at `start` for this many seconds (start/end ignored)

    @property
    def out_len(self) -> float:
        return self.freeze if self.freeze > 0 else (self.end - self.start) / self.rate


class TimeMap:
    """Maps a time on the source clip to the rebuilt clip."""

    def __init__(self, segments: Sequence[Segment]) -> None:
        self.segments = list(segments)

    def __call__(self, t: float) -> float:
        out = 0.0
        for s in self.segments:
            if s.freeze > 0:
                if t > s.start:
                    out += s.freeze
                continue
            if t <= s.start:
                break
            out += (min(t, s.end) - s.start) / s.rate
        return out

    @property
    def duration(self) -> float:
        return sum(s.out_len for s in self.segments)


def _atempo(rate: float) -> str:
    parts, r = [], rate
    while r > 2.0:
        parts.append("atempo=2.0")
        r /= 2.0
    while r < 0.5:
        parts.append("atempo=0.5")
        r /= 0.5
    parts.append(f"atempo={r:.4f}")
    return ",".join(parts)


def retime(clip: Path, out: Path, segments: Sequence[Segment], fps: int = 30) -> tuple[Path, TimeMap]:
    chains, labels = [], []
    for i, s in enumerate(segments):
        if s.freeze > 0:
            chains.append(f"[0:v]trim=start={s.start:.3f}:duration={1 / fps:.4f},setpts=PTS-STARTPTS,"
                          f"tpad=stop_mode=clone:stop_duration={s.freeze:.3f},trim=duration={s.freeze:.3f},"
                          f"setpts=PTS-STARTPTS[v{i}]")
            chains.append(f"aevalsrc=0|0:s=48000:d={s.freeze:.3f},aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        else:
            chains.append(f"[0:v]trim=start={s.start:.3f}:end={s.end:.3f},setpts=(PTS-STARTPTS)/{s.rate:.4f}[v{i}]")
            chains.append(f"[0:a]atrim=start={s.start:.3f}:end={s.end:.3f},asetpts=PTS-STARTPTS,"
                          f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,{_atempo(s.rate)}[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(chains) + f";{''.join(labels)}concat=n={len(segments)}:v=1:a=1[v][a]"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
         "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", str(out)], timeout=900)
    return out, TimeMap(segments)


# --------------------------------------------------------------------------- compositing

@dataclass(frozen=True)
class Placed:
    png: Path
    start: float
    end: float
    x: int | str = "(W-w)/2"   # ffmpeg overlay expressions allowed
    y: int | str = 260


def overlay(clip: Path, out: Path, items: Sequence[Placed]) -> Path:
    if not items:
        return clip
    inputs: list[str] = ["-i", str(clip)]
    chain, last = [], "[0:v]"
    for i, p in enumerate(items, 1):
        inputs += ["-loop", "1", "-i", str(p.png)]
        tag = f"[o{i}]"
        chain.append(f"{last}[{i}:v]overlay=x={p.x}:y={p.y}:shortest=1:"
                     f"enable='between(t,{p.start:.3f},{p.end:.3f})'{tag}")
        last = tag
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(chain), "-map", last, "-map", "0:a?",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)],
        timeout=900)
    return out


def split_layout(clip: Path, out: Path, top_png: Path, top_h: int, bg: str) -> Path:
    """Gameplay scaled into the lower (H - top_h) px, centered on `bg`, banner on top."""
    gh = H - top_h
    gw = int(round(W * gh / H / 2) * 2)
    graph = (f"color=c={bg}:s={W}x{H}:r=30[base];[0:v]scale={gw}:{gh}[g];"
             f"[base][g]overlay=x=(W-w)/2:y={top_h}:shortest=1[b];[b][1:v]overlay=0:0[v]")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-loop", "1", "-i", str(top_png), "-filter_complex", graph,
         "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "copy", "-shortest", str(out)], timeout=900)
    return out


def clip_duration(clip: Path) -> float:
    return float(probe(clip)["format"]["duration"])
