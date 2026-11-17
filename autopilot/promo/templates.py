"""Phase-2 template effects: turn a raw capture + PromoBot events into the edited gameplay clip.

Each preparer returns the processed clip path (or the original clip when its events are
missing, so a capture from an older PromoBot still renders as a plain showcase).

  timed_challenge   (Math)  "Can you solve it?" + 3-2-1 countdown during the bot's think pause
  logic_execution   (Code)  block-building at 1.5x, the program run at 1x with a "Run it!" pop
  brain_teaser      (IQ)    top banner hook + gameplay below, countdown on the first puzzle
  timelapse_reveal  (Draw)  drawing sped up (to ~10 s, max 4x), "Ta-da!" on the reveal at 1x
  musical_showcase  (Keys / Frets)  game audio kept; caption sticker for the first seconds
  experiment_wow    (Lab)   "Experiment time!" pop, then the reaction at 1x
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

from autopilot.promo import effects as fx

log = logging.getLogger("autopilot.promo.templates")


def _states(manifest: dict[str, Any]) -> list[tuple[float, str]]:
    return [(float(e["t"]), str(e["state"])) for e in manifest.get("events", []) if e.get("type") == "state"]


def _first(states: list[tuple[float, str]], pred: Callable[[str], bool]) -> Optional[float]:
    return next((t for t, s in states if pred(s)), None)


class Style:
    def __init__(self, app: dict[str, Any]) -> None:
        theme = app.get("theme", {})
        self.fg = theme.get("textColor", "#3B2F2F")
        self.accent = theme.get("accentColor", "#F08A7E")
        self.primary = theme.get("primaryColor", "#5FAE8C")
        self.bg = theme.get("backgroundColor", "#FEF8F1")
        fonts = Path(app.get("path", ".")) / "assets" / "fonts"
        cands = [Path(app["sticker_font"])] if app.get("sticker_font") else []   # CJK locales
        cands += [fonts / "Quicksand-Variable.ttf", fonts / "NunitoSans-Variable.ttf"]
        self.font = next((str(p) for p in cands if p.is_file()), None)
        self.words = app.get("stickers", {})

    def word(self, key: str, default: str, **fmt) -> str:
        return str(self.words.get(key, default)).format(**fmt)

    def tag(self, text: str, out: Path, **kw) -> Path:
        return fx.sticker(text, out, font_path=self.font, fg=self.fg, accent=self.accent, **kw)

    def count(self, n: int, out: Path) -> Path:
        return fx.sticker(str(n), out, font_path=self.font, fg="#FFFFFF", bg=self.accent, accent="#FFFFFF",
                          size=150, circle=True)


def _countdown(style: Style, work: Path, answer_at: float, seconds: int = 3) -> list[fx.Placed]:
    out = []
    for i, n in enumerate(range(seconds, 0, -1)):
        start = answer_at - seconds + i
        if start >= 0:
            out.append(fx.Placed(style.count(n, work / f"count_{n}.png"), start, start + 0.95,
                                 x="W-w-150", y=300))
    return out


def timed_challenge(app, manifest, work: Path, copy) -> Path:
    clip = Path(manifest["clip_path"])
    st = _states(manifest)
    asked = _first(st, lambda s: s == "p0:asking")
    answered = _first(st, lambda s: s == "p0:answered")
    if asked is None or answered is None:
        return clip
    style = Style(app)
    if answered - asked < 2.5:  # bot did not think: freeze before the tap so the countdown fits
        hold = 3.0 - (answered - asked) + 0.5
        cut = max(asked, answered - 0.1)
        clip, tmap = fx.retime(clip, work / "retimed.mp4", [fx.Segment(0, cut), fx.Segment(cut, freeze=hold),
                                                           fx.Segment(cut, fx.clip_duration(clip))])
        asked, answered = tmap(asked), tmap(answered)
    items = [fx.Placed(style.tag(copy.get("challenge") or style.word("challenge", "Can you solve it?"), work / "ask.png"), asked + 0.3,
                       answered, y=230)]
    items += _countdown(style, work, answered)
    return fx.overlay(clip, work / "challenge.mp4", items)


def logic_execution(app, manifest, work: Path, copy) -> Path:
    clip = Path(manifest["clip_path"])
    running = _first(_states(manifest), lambda s: s == "RUNNING")
    if running is None or running < 1.5:
        return clip
    total = fx.clip_duration(clip)
    clip, tmap = fx.retime(clip, work / "ramped.mp4", [fx.Segment(0, running, rate=1.5), fx.Segment(running, total)])
    style = Style(app)
    t = tmap(running)
    return fx.overlay(clip, work / "logic.mp4", [fx.Placed(style.tag(style.word("run", "Run it!"), work / "run.png"), t, t + 1.4, y=240)])


def brain_teaser(app, manifest, work: Path, copy) -> Path:
    clip = Path(manifest["clip_path"])
    style = Style(app)
    top_h = 420
    top = fx.banner(copy.get("hook_overlay") or style.word("challenge", "Can you solve it?"), work / "banner.png", height=top_h,
                    font_path=style.font, bg=style.bg, fg=style.fg, accent=style.accent)
    framed = fx.split_layout(clip, work / "split.mp4", top, top_h, style.bg)
    st = _states(manifest)
    q0 = _first(st, lambda s: s == "q0")
    q1 = _first(st, lambda s: s in ("q1", "stop"))
    if q0 is None or q1 is None or q1 - q0 < 3.5:
        return framed
    # The bot answers ~NEXT_DELAY before the next puzzle appears; count down to just before that.
    return fx.overlay(framed, work / "teaser.mp4", _countdown(style, work, q1 - 1.0))


def timelapse_reveal(app, manifest, work: Path, copy) -> Path:
    clip = Path(manifest["clip_path"])
    st = _states(manifest)
    drawing = _first(st, lambda s: s == "drawing")
    reveal = _first(st, lambda s: s == "reveal")
    if drawing is None or reveal is None:
        return clip
    span = reveal - drawing
    rate = min(4.0, max(1.0, span / 10.0))  # ~10 s of drawing: a timelapse that still reads as a hand
    total = fx.clip_duration(clip)
    segs = [fx.Segment(0, drawing)] if drawing > 0.05 else []
    segs += [fx.Segment(drawing, reveal, rate=rate), fx.Segment(reveal, total)]
    clip, tmap = fx.retime(clip, work / "timelapse.mp4", segs)
    style = Style(app)
    t = tmap(reveal)
    label = style.word("speed", "{rate}x speed", rate=f"{rate:.0f}") if rate >= 1.5 else style.word("step_by_step", "Step by step")
    items = [fx.Placed(style.tag(label, work / "speed.png"),
                       tmap(drawing) + 0.2, t, x="W-w-150", y=240),
             fx.Placed(style.tag(style.word("tada", "Ta-da!"), work / "tada.png", size=96), t + 0.2, t + 2.2, y=260)]
    return fx.overlay(clip, work / "reveal.mp4", items)


def musical_showcase(app, manifest, work: Path, copy) -> Path:
    clip = Path(manifest["clip_path"])
    caption = app.get("caption") or copy.get("hook_overlay")
    if not caption:
        return clip
    style = Style(app)
    return fx.overlay(clip, work / "music.mp4", [fx.Placed(style.tag(caption, work / "caption.png"), 0.3, 4.5, y=240)])


def experiment_wow(app, manifest, work: Path, copy) -> Path:
    clip = Path(manifest["clip_path"])
    style = Style(app)
    return fx.overlay(clip, work / "wow.mp4",
                      [fx.Placed(style.tag(style.word("experiment", "Experiment time!"), work / "wow.png"), 0.3, 2.8, y=240)])


PREPARERS: dict[str, Callable] = {
    "timed_challenge": timed_challenge,
    "logic_execution": logic_execution,
    "brain_teaser": brain_teaser,
    "timelapse_reveal": timelapse_reveal,
    "musical_showcase": musical_showcase,
    "experiment_wow": experiment_wow,
}


def prepare(app: dict[str, Any], manifest: dict[str, Any], work: Path, copy: dict[str, str]) -> Path:
    fn = PREPARERS.get(manifest.get("template", ""))
    if fn is None:
        return Path(manifest["clip_path"])
    work.mkdir(parents=True, exist_ok=True)
    try:
        return fn(app, manifest, work, copy)
    except Exception as exc:  # an effect failing must not cost the whole promo
        log.warning("%s effects failed (%s); using the raw capture", manifest.get("template"), exc)
        return Path(manifest["clip_path"])
