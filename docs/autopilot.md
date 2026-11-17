# Autopilot: unattended topic → MP4 → social posts

Implements [requirements.md](requirements.md) on top of OpenMontage without changing its core.
For the Kawaii app promos (Godot capture, languages, nightly runs) see
[promo_pipeline.md](promo_pipeline.md).

```
run_pipeline.py ──► autopilot/planner     script + scene plan (JSON)  ─┐
                    autopilot/narration   Piper TTS per scene          │  LLM calls go through
                    autopilot/visuals     Pexels video → stills → card │  llm_router.py
                    autopilot/audio_mix   music −18 dB under voice     │  (Groq → AI Studio → Ollama)
                    autopilot/render      Remotion via video_compose  ◄┘
                    autopilot/qa          >1 MB, audio, resolution, black frames, silence
                    autopilot/metadata    per-platform title/caption/hashtags
                    autopilot/publishers  YouTube · Facebook Reels · Instagram Reels · TikTok
                    autopilot/db          SQLite: runs, LLM provider/fallbacks, post URLs
```

## One-time setup

Python 3.12 is required on Intel Macs (Piper's `onnxruntime` has no newer Intel wheels).

```bash
uv venv --python 3.12 .venv
VIRTUAL_ENV=$PWD/.venv uv pip install -r requirements.txt -r requirements-autopilot.txt
python -m piper.download_voices --download-dir ~/.piper/models en_US-lessac-medium
(cd remotion-composer && npm install)
```

Fill in `.env`:

| What | Keys |
|---|---|
| LLM (at least one) | `GROQ_API_KEY`, `LLM_GEMINI_API_KEY`, or a running `ollama serve` |
| Stock footage | `PEXELS_API_KEY` (free). Wikimedia Commons needs no key. |
| YouTube | OAuth Desktop client JSON at `secrets/yt_client.json`, then `python run_pipeline.py --authorize youtube` |
| Facebook / Instagram | `META_ACCESS_TOKEN` (Page token), `META_PAGE_ID`, `META_IG_USER_ID` |
| TikTok | `TIKTOK_ACCESS_TOKEN` |

Optional: drop royalty-free tracks into `music_library/`; one is picked per video.

## Running

```bash
.venv/bin/python llm_router.py &                       # keep running (or as a service)
.venv/bin/python run_pipeline.py --topic "Why octopuses have three hearts" --dry-run
.venv/bin/python run_pipeline.py --topic "..." --platforms youtube,facebook
.venv/bin/python run_pipeline.py --topic "..." --aspect 16:9 --duration 5m --template listicle --platforms youtube
.venv/bin/python run_pipeline.py --topic "..." --template news_recap --facts notes.txt --platforms meta,tiktok
```

Each run gets a workspace in `projects/<slug>-<timestamp>/` (visible in Backlot under the
"autopilot" series) with `artifacts/plan.json`, `captions.json`, `shots.json`,
`qa_report.json`, `publish_metadata.json` (incl. stock credits) and `renders/final.mp4`.

Exit codes: `0` published/rendered, `1` failed before publishing, `2` one or more platforms failed.

## Platform notes

- **Privacy defaults are conservative.** `YOUTUBE_PRIVACY=private` and `TIKTOK_PRIVACY_LEVEL=SELF_ONLY`.
  Unaudited Google API projects and TikTok apps are restricted to private posts anyway; switch
  after your app passes review.
- **AI disclosure is on by default** (`YOUTUBE_SYNTHETIC_MEDIA`, `TIKTOK_IS_AIGC`).
- Facebook Reels must be 9:16. Instagram uses the resumable upload flow, which needs a token from
  Facebook Login for Business.
- `news_recap` only states facts from `--facts`; there is no automatic web research step.

## Inspecting runs

```bash
sqlite3 projects/autopilot.sqlite3 "select id, project_id, status, error from runs order by id desc limit 5"
sqlite3 projects/autopilot.sqlite3 "select run_id, stage, provider, fallbacks from llm_calls order by id desc limit 10"
sqlite3 projects/autopilot.sqlite3 "select run_id, platform, status, url, error from posts order by id desc limit 10"
```

## Scheduling

Example crontab (daily at 09:00, router started separately):

```
0 9 * * * cd /path/to/OpenMontage && .venv/bin/python run_pipeline.py --topic "$(head -1 topics.txt)" --platforms youtube,meta,tiktok >> projects/autopilot.log 2>&1 && sed -i '' 1d topics.txt
```
