# Kawaii promo pipeline: setup, daily runs, operations

Automated 9:16 promo videos for the eight Kawaii apps: the app plays itself in Godot, the
video is assembled with a voiceover, captions and an end card, checked, and (when you
allow it) posted to YouTube Shorts, Facebook Reels, Instagram Reels and TikTok.

Spec: [kawaii_ai_video_pipeline_prd.md](kawaii_ai_video_pipeline_prd.md).
Related: [autopilot.md](autopilot.md) (topic → stock-footage videos, same publishers),
[promo_stitch_prompts.md](promo_stitch_prompts.md) (end-card designs).

---

## 1. How it works

```
 schedule.yaml ──► scripts/promo_daily.py  (launchd, every night)
                        │  picks today's jobs + next content item (rotation)
                        ▼
 ┌─ Stage 1: capture (Godot Movie Maker, on the Mac) ───────────────────────────┐
 │  games/kawaii-<app>/tests/promo/promo.tscn runs the app's own --autoplay bot  │
 │  at a person-like --pace, records 1080x1920 @30fps + game audio, and logs     │
 │  an event timeline (problem asked / answered, program running, reveal ...)   │
 │  → projects/promo_inbox/<app>_<item>_<locale>_<time>.mp4 + .json manifest     │
 └──────────────────────────────────────────────────────────────────────────────┘
                        ▼
 ┌─ Stage 2: assemble (OpenMontage) ────────────────────────────────────────────┐
 │  template effects (countdown, speed ramp, split layout, stickers)            │
 │  hook card + parent voiceover → gameplay (game audio) → end card + outro VO  │
 │  copy: reviewed text from locales/<locale>.yaml (or LLM router, optional)    │
 │  Remotion render → QA (size, audio, resolution, black frames, silence)        │
 │  → projects/promo-<app>-<item>-<locale>-<time>/renders/final.mp4             │
 └──────────────────────────────────────────────────────────────────────────────┘
                        ▼
 ┌─ Stage 3: publish (only if publish: true AND copy review: approved) ─────────┐
 │  YouTube Data API v3 · Meta Graph API (FB + IG Reels) · TikTok Content API   │
 │  every run, LLM call and post is logged in projects/autopilot.sqlite3        │
 └──────────────────────────────────────────────────────────────────────────────┘
```

Three languages meet in one promo:

| Setting | Controls | Where |
|---|---|---|
| `locale` | the parent's language: voiceover, captions, stickers, end card, titles; also the app's UI language in the capture | `--locale`, `schedule.yaml` jobs |
| `ui` | app interface language (defaults to the locale's) | `--ui` |
| `target` | what the child learns (Kawaii Stories only) | `--target`, rotation |

Templates per app (PRD section 4):

| App | Template | What the video does |
|---|---|---|
| Stories | `story_cliffhanger` | cover art + hook → story read aloud → stops on the choice → "Where will Bun go next?" |
| Math | `timed_challenge` | "Can you solve it?" + 3-2-1 countdown while the bot thinks, then the answer |
| Code | `logic_execution` | blocks placed one by one (1.5x), program runs at 1x with "Run it!" |
| IQ | `brain_teaser` | hook banner on top, puzzle below, countdown on the first puzzle |
| Lab | `experiment_wow` | "Experiment time!" then the experiment at person pace |
| Draw | `timelapse_reveal` | hand-speed drawing sped up (max 4x, labelled) → "Ta-da!" reveal |
| Keys / Frets | `musical_showcase` | the song plays in the rhythm game; game audio kept, caption sticker |

---

## 2. Where things live

**OpenMontage** (`~/Documents/projects/daian/OpenMontage`, branch `feat/llm-router-autopilot`)

| Path | What |
|---|---|
| `run_promo.py` | manual CLI: `capture`, `assemble`, `run`, `batch`, `apps` |
| `scripts/promo_daily.py` | the nightly runner (schedule + rotation + lock + summary) |
| `scripts/install_launchd.sh`, `scripts/launchd/*.plist` | macOS scheduling |
| `autopilot/promo/apps.yaml` | apps: repo, template, capture args, pace, theme, English copy and claims |
| `autopilot/promo/locales/<locale>.yaml` | per language: voice, sticker words, translated claims / copy / CTA, `review` status |
| `autopilot/promo/schedule.yaml` | what runs which weekday, content rotation, publish switch |
| `autopilot/promo/{capture,assemble,templates,effects,locale}.py` | the pipeline |
| `autopilot/publishers/` | YouTube / Meta / TikTok uploaders |
| `llm_router.py` | optional LLM gateway (Groq → AI Studio → Ollama) for generated copy |
| `projects/` (git-ignored) | captures, renders, logs, `autopilot.sqlite3`, `promo_state.json` |

**Each app repo** (`~/Documents/projects/daian/games/kawaii-<app>`)

| Path | What |
|---|---|
| `tests/promo/promo.tscn`, `promo.gd`, `promo_bot.gd` | the PromoBot (excluded from exports) |
| `tests/promo/promo_watch.gd` | per-app event watcher (math, code, iq, draw) |
| `design/promo/end_card[_<locale>].png` | Stitch end card (optional; text card if missing) |
| debug flags `--pace`, `--think`, `--hold-choice` | person-like bot timing; debug builds only |

---

## 3. One-time setup

Do these once on the Mac that will run the promos (captures need its screen and GPU).

### 3.1 Tools

```bash
cd ~/Documents/projects/daian/OpenMontage
uv venv --python 3.12 .venv            # 3.12: Piper's onnxruntime has no newer Intel-Mac build
VIRTUAL_ENV=$PWD/.venv uv pip install -r requirements.txt -r requirements-autopilot.txt fonttools
(cd remotion-composer && npm install && npx remotion browser ensure)
.venv/bin/python -m piper.download_voices --download-dir ~/.piper/models en_US-lessac-medium vi_VN-vais1000-medium
```

Also needed and already present on this Mac: Godot 4.7 at `/Applications/Godot.app`, `ffmpeg`
(with libx264), Node 22+.

The first Remotion render after installing can time out while macOS scans the new headless
browser; just run it again.

### 3.2 Keys and accounts (`.env` in OpenMontage)

| Need | Keys | Notes |
|---|---|---|
| ElevenLabs (zh / ja voiceovers) | `ELEVENLABS_API_KEY`, or the existing `~/.elevenlabs_key` | paid; only used with `--allow-paid-voice` / `allow_paid_voice: true` |
| LLM copy (optional) | `GROQ_API_KEY`, `LLM_GEMINI_API_KEY`, or Ollama | only if `use_llm: true`; the reviewed locale copy is the default |
| YouTube | OAuth Desktop client JSON at `secrets/yt_client.json`, then `.venv/bin/python run_pipeline.py --authorize youtube` | unaudited Google projects upload as private |
| Facebook + Instagram | `META_ACCESS_TOKEN` (long-lived Page token), `META_PAGE_ID`, `META_IG_USER_ID` | needs Facebook Login for Business; IG must be a Business/Creator account linked to the Page |
| TikTok | `TIKTOK_ACCESS_TOKEN`, `TIKTOK_POST_MODE`, `TIKTOK_PRIVACY_LEVEL` | unaudited apps can post `SELF_ONLY` only |

Safety defaults already in `.env`: `YOUTUBE_PRIVACY=private`, `YOUTUBE_SYNTHETIC_MEDIA=true`,
`TIKTOK_PRIVACY_LEVEL=SELF_ONLY`, `TIKTOK_IS_AIGC=true`. Change them only when you are ready.

Not covered by these settings: a Claude Pro subscription has no API key, so it cannot drive the
router. Use Claude Code (on the subscription) to write and update the copy in `locales/*.yaml`.

### 3.3 End cards (optional but recommended)

Design one per app (and per language) in Stitch with the prompts in
[promo_stitch_prompts.md](promo_stitch_prompts.md); export to
`games/kawaii-<app>/design/promo/end_card.png` / `end_card_vi.png` / ….

### 3.4 Smoke test

```bash
cd ~/Documents/projects/daian/OpenMontage
.venv/bin/python run_promo.py apps                                   # apps × languages, review status
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python run_promo.py run --app stories --locale vi --target zh --dry-run --no-llm
open projects/promo-stories-*/renders/final.mp4                      # QuickTime (VS Code's player has no AAC audio)
```

---

## 4. Running by hand

Always run from the OpenMontage folder with the venv on `PATH` (Piper is a venv command):

```bash
export PATH="$PWD/.venv/bin:$PATH"
```

| Task | Command |
|---|---|
| Status of every app × language | `.venv/bin/python run_promo.py apps` |
| One promo, render only | `.venv/bin/python run_promo.py run --app math --locale vi --dry-run --no-llm` |
| Pick the content | `... --app keys --param song=sakura` · `--app stories --story ch03_rainy_library --target ja` · `--app math --param lesson=g2_m3_l03` |
| Whole language, every app | `.venv/bin/python run_promo.py batch --locale vi --dry-run --no-llm` (Stories once per learning language) |
| Capture once, re-assemble many times | `run_promo.py capture --app iq --locale vi` → `run_promo.py assemble --manifest projects/promo_inbox/<file>.json --dry-run --no-llm` |
| Publish one reviewed promo | `.venv/bin/python run_promo.py run --app math --locale vi --platforms youtube,meta,tiktok --no-llm` |
| Japanese / Chinese voiceover | add `--allow-paid-voice` (ElevenLabs credits) |
| Run tonight's jobs now | `.venv/bin/python scripts/promo_daily.py` (or `--day sat`, `--plan`, `--dry-run`) |

`--platforms` accepts `youtube`, `facebook`, `instagram`, `tiktok`, or `meta` (= facebook + instagram).

---

## 5. Review and approval (before anything is published)

Two gates protect the channels:

1. **Copy gate (per app × language).** `locales/<locale>.yaml` → `review: draft | approved`.
   A draft renders but publishing refuses it (`PermissionError ... not approved`).
   Workflow: Claude drafts → Gemini Pro review → native speaker → set `review: approved`.
   Check the `approved_claims` for meaning, not only grammar: they are the only product
   statements a promo may make (no prices, "free", rankings or statistics).
2. **Publish switch.** `schedule.yaml` → `publish: false` (default) renders every night without
   posting. Watch a week of renders, then set `publish: true`.

Copy source: with `use_llm: false` (default) the voiceover lines are exactly the reviewed
`fallback_copy`. With `use_llm: true` the LLM writes fresh lines each night (length and banned-word
checked, but not human-reviewed), so keep it off for published languages until you trust it.

YouTube "made for kids": `made_for_kids: false` per app in `apps.yaml` because the promos speak to
parents. Keep the hook and outro parent-directed; if a video becomes mainly a kids' episode, set
it to `true` for that app.

---

## 6. Run it every day (macOS launchd)

### 6.1 Install

```bash
cd ~/Documents/projects/daian/OpenMontage
./scripts/install_launchd.sh            # router always on + nightly run at 02:30
./scripts/install_launchd.sh 3 15       # ... or at 03:15
```

This writes two LaunchAgents to `~/Library/LaunchAgents/` and starts them:

| Job | What | When |
|---|---|---|
| `com.daian.llm-router` | `llm_router.py` on 127.0.0.1:4000 | at login, restarted if it exits |
| `com.daian.promo-daily` | `scripts/promo_daily.py` | every day at the chosen time |

Wake the Mac before the run (the installer prints the exact line):

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 02:25:00
```

Requirements for the nightly run:
- **Stay logged in** (screen lock is fine, but not "Log Out"): captures open a Godot window
  in your session, which is why these are LaunchAgents, not system daemons. Before relying on
  it, test once with the screen locked: `launchctl kickstart gui/$(id -u)/com.daian.promo-daily`.
- Keep the Mac on power. If it was asleep at run time, launchd runs the job when it wakes.
- Don't run another Godot capture of the same app at the same time (a lock file prevents two
  nightly runs from overlapping).

### 6.2 What runs when

`autopilot/promo/schedule.yaml`:

```yaml
publish: false
platforms: [youtube, facebook, instagram, tiktok]
use_llm: false
allow_paid_voice: false
days:
  mon: [{app: stories, locale: vi}, {app: math, locale: vi}]
  ...
rotation:
  stories: {story: [ch01_morning_walk, ...], target: [zh, en, ja]}
  keys: {song: [twinkle, mary_lamb, ...]}
```

- Each job takes the **next** item of its app's rotation, so every day shows new content.
  Progress is kept in `projects/promo_state.json` (delete a key to restart that app's rotation).
- The rotation only advances after a successful render; a failed night is retried next time.
- Budget ~15-20 min per promo on this Mac; 2-3 jobs per night is comfortable.
- `--plan` shows what a day would run: `.venv/bin/python scripts/promo_daily.py --plan --day fri`.
- Keys / Frets rotations contain only public-domain or Kawaii-original songs (avoids Content ID).

### 6.3 Daily check (2 minutes)

```bash
cat projects/promo_daily/$(date +%F).log | tail -20         # summary: OK / FAIL per job + video path
open $(ls -td projects/promo-*/renders | head -1)/final.mp4    # newest render
sqlite3 projects/autopilot.sqlite3 \
  "select run_id, platform, status, url, error from posts order by id desc limit 10"
```

A macOS notification ("Kawaii promos: 2/2 ok") appears when the run finishes.

### 6.4 Manage the schedule

| Task | Command |
|---|---|
| Run tonight's job now | `launchctl kickstart gui/$(id -u)/com.daian.promo-daily` |
| Is it installed / last result | `launchctl print gui/$(id -u)/com.daian.promo-daily \| grep -E "state\|last exit"` |
| Router health | `curl -s 127.0.0.1:4000/health \| python3 -m json.tool` |
| Pause posting, keep rendering | `publish: false` in `schedule.yaml` (read fresh every night) |
| Stop everything | `./scripts/install_launchd.sh --uninstall` |
| Logs | `projects/promo_daily/<date>.log`, `projects/logs/promo_daily.out.log`, `projects/logs/llm_router.log` |

---

## 7. Extending

**New content for an existing app:** add ids to its list in `schedule.yaml → rotation`.
Stories needs recorded narration for the story (`games/kawaii-stories/assets/audio/narration/<lang>/<story>`).

**New language** (e.g. Chinese parents):
1. `locales/zh.yaml` already holds the voice (ElevenLabs, eleven_v3) and stickers; add an `apps:`
   section like `vi.yaml` (claims, fallback copy, CTA, `review: draft`). Ask Claude Code to draft it.
2. Review → `review: approved`.
3. Add jobs with `locale: zh` to `schedule.yaml` and set `allow_paid_voice: true`
   (ElevenLabs: two short lines per video).

**New app:** PromoBot files into `tests/promo/` (copy from any app), add a watcher if the template
needs events, then an entry in `apps.yaml` (capture args with `--pace`, claims, fallback copy) and
in each `locales/*.yaml`.

**Faster renders / more videos:** run the same setup on a faster Mac, or move Stage 2 (render) to
a server and keep only Stage 1 (capture) on the Mac. Linux captures need a GPU or Xvfb + Vulkan.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Video plays without sound in VS Code | VS Code's player has no AAC; open in QuickTime |
| UI clipped at the left / right edge | capture window not borderless; `capture.py` writes a temporary `override.cfg` with `borderless=true` — make sure no stale `override.cfg` sits in the app repo |
| `override.cfg already exists; refusing` | a capture was killed mid-run; delete `games/kawaii-<app>/override.cfg` |
| `Godot exited without PROMO END` | the app's scene / flags changed; run the capture command from the log by hand and read Godot's output |
| `Timed out ... connect to the browser` | first Remotion launch after install; run again |
| `Remotion render timed out` | another heavy job shared the CPU; rerun, or set `REMOTION_TIMEOUT_MS` / `REMOTION_CONCURRENCY` |
| `... copy is 'draft', not approved` | review the locale file and set `review: approved` (render with `--dry-run` meanwhile) |
| `uses ElevenLabs (paid) ... --allow-paid-voice` | zh / ja voiceover needs explicit permission to spend credits |
| `QA failed: ... black frames` / `near-silent` | look at `artifacts/qa_report.json`; usually the bot stalled (wrong lesson id) — try another rotation item |
| Bot too fast / too slow | `--pace` / `--think` defaults per app in `apps.yaml → capture.defaults` |
| Nothing ran overnight | Mac logged out or asleep without `pmset` wake; check `launchctl print ...` and `projects/logs/promo_daily.out.log` |
| Publish failed for one platform | others still post; see `posts` in `autopilot.sqlite3` and `artifacts/publish_report.json`; tokens expire (Meta ~60 days, TikTok refresh) |

---

## 9. Costs

| Item | Cost |
|---|---|
| Capture, Piper voices (en, vi), Remotion, ffmpeg, Wikimedia | free, local |
| ElevenLabs (zh / ja voiceovers) | two short lines per video, billed per character on your plan |
| LLM copy (optional) | Groq / AI Studio free tiers, or local Ollama |
| Platforms | free APIs; app review needed for public TikTok posts / unlocked YouTube privacy |
