#!/usr/bin/env python3
"""Unattended topic -> rendered MP4 -> social posts (see docs/requirements.md).

Examples:
  python run_pipeline.py --topic "Why octopuses have three hearts" --platforms youtube,facebook
  python run_pipeline.py --topic "..." --aspect 16:9 --duration 5m --template listicle --dry-run
  python run_pipeline.py --topic "..." --template news_recap --facts notes.txt --platforms meta,tiktok
  python run_pipeline.py --authorize youtube        # one-time OAuth for YouTube uploads

Requires the LLM router to be running (python llm_router.py) and
config.yaml llm.provider = router.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import tools.base_tool  # noqa: E402,F401  (loads .env)


def parse_duration(raw: str) -> float:
    raw = raw.strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return float(raw)
    m = re.fullmatch(r"(?:(\d+)m)?\s*(?:(\d+)s)?", raw)
    if not m or not any(m.groups()):
        raise argparse.ArgumentTypeError(f"bad duration {raw!r}; use 45, 45s, 3m or 3m30s")
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topic", help="what the video is about")
    ap.add_argument("--platforms", default="", help="comma list: youtube,facebook,instagram,tiktok (meta = fb+ig)")
    ap.add_argument("--aspect", default="9:16", choices=["9:16", "16:9"])
    ap.add_argument("--duration", type=parse_duration, default=None,
                    help="target length: 45, 45s, 3m, 3m30s (default 45s for 9:16, 4m for 16:9)")
    ap.add_argument("--template", default="explainer", choices=["explainer", "listicle", "news_recap"])
    ap.add_argument("--facts", help="text file with source notes (required for trustworthy news_recap)")
    ap.add_argument("--music", help="music file path, or 'none' (default: random track from music_library/)")
    ap.add_argument("--voice", help="Piper voice id (default PIPER_VOICE or en_US-lessac-medium)")
    ap.add_argument("--project-id", help="workspace name under projects/ (default: slug + timestamp)")
    ap.add_argument("--dry-run", action="store_true", help="render + QA + metadata, but do not publish")
    ap.add_argument("--authorize", choices=["youtube"], help="run one-time OAuth setup and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "urllib3", "google"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.authorize == "youtube":
        from autopilot.publishers.youtube import authorize
        print(f"YouTube token saved to {authorize()}")
        return 0
    if not args.topic:
        ap.error("--topic is required")

    from autopilot.pipeline import RunOptions, run
    from autopilot.publishers import parse_platforms

    try:
        platforms = parse_platforms(args.platforms)
    except ValueError as exc:
        ap.error(str(exc))
    if args.template == "news_recap" and not args.facts:
        logging.warning("news_recap without --facts: the script will avoid specifics it cannot verify")
    facts = Path(args.facts).read_text(encoding="utf-8") if args.facts else None
    seconds = args.duration or (45 if args.aspect == "9:16" else 240)

    try:
        result = run(RunOptions(topic=args.topic, platforms=platforms, aspect=args.aspect, seconds=seconds,
                                template=args.template, facts=facts, music=args.music, voice=args.voice,
                                project_id=args.project_id, dry_run=args.dry_run))
    except Exception as exc:
        logging.error("Run failed: %s", exc)
        return 1

    print(f"\nStatus:  {result.status}\nVideo:   {result.video}\nProject: {result.project_dir}")
    for p in result.posts:
        print(f"  {p.platform:<10} {p.status:<10} {p.url or p.error or ''}")
    return 0 if result.status in {"published", "rendered"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
