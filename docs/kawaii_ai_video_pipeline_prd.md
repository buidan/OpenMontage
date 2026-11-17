# Product Requirements Document (PRD): Kawaii AI Video Production & Multi-Platform Distribution Pipeline

**Version:** 3.0  
**Target Engine:** Godot 4.7 (`--write-movie` Native CLI)  
**Covered Apps (8):** `Kawaii Stories`, `Kawaii Math`, `Kawaii Code`, `Kawaii IQ`, `Kawaii Lab`, `Kawaii Draw`, `Kawaii Keys (Piano)`, `Kawaii Frets (Guitar)`  
**Architecture Core:** OpenMontage + Multi-Provider LLM Router (Groq, Google AI Studio, Ollama) + Godot Auto-Capture  

---

## 1. Executive Summary
This system automates the end-to-end creation, editing, and multi-platform publishing of short-form promotional videos (`9:16` vertical Reels/Shorts) for an ecosystem of 8 educational kids' apps built in **Godot 4.7**. 

To maximize organic reach without recurring SaaS overhead, the architecture combines **Godot's native offline movie renderer** for automated gameplay capture, an **LLM Router** supporting manual and automatic fallback (Groq Cloud -> Google AI Studio -> Local Ollama), **OpenMontage** (Remotion & FFmpeg) for automated video assembly, and automated API distribution to YouTube Shorts, Facebook Reels, Instagram Reels, and TikTok.

---

## 2. System Architecture & Workflow

```text
┌───────────────────────────────────────────────────────────────────────┐
│ STAGE 1: Godot 4.7 Automated Promo Capture (CLI Batch Runner)         │
│ • Launches Godot 4.7 project with `--write-movie` & `--fixed-fps 60`  │
│ • Injects `--promo-mode=challenge` to auto-play levels via PromoBot   │
│ • Outputs raw AVI/PNG -> FFmpeg normalizes to H.264 + AAC `.mp4`      │
│ • Emits clip + `metadata.json` sidecar to `/inbox`                    │
└──────────────────────────────────┬────────────────────────────────────┘
                                   │
                                   ▼
┌───────────────────────────────────────────────────────────────────────┐
│ STAGE 2: OpenMontage Promo Assembler & LLM Router                     │
│ • Reads `metadata.json` -> generates parent/kid hook via Router       │
│ • LLM Failover Chain: Groq Cloud ➔ Google AI Studio ➔ Local Ollama   │
│ • Voiceover: Piper TTS (Free Offline) + Game Audio Mixing             │
│ • Composes 9:16 Reel (gameplay frame, captions, CTA end-card)         │
└──────────────────────────────────┬────────────────────────────────────┘
                                   │
                                   ▼
┌───────────────────────────────────────────────────────────────────────┐
│ STAGE 3: Multi-Platform Publisher Service                             │
│ • YouTube Data API v3 (Shorts #tag)                                   │
│ • Meta Graph API (Facebook Reels + Instagram Business Reels)          │
│ • TikTok Content Posting API (Direct Post)                            │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 3. Functional Requirements: Stage 1 (Godot 4.7 Auto-Capture)

### FR-G1: Standardized CLI Capture Contract
Every Kawaii Godot 4.7 repository must support a standardized command-line invocation for batch recording:
```bash
godot --path /projects/kawaii-math \
  --write-movie /tmp/raw_capture/kawaii_math_01.avi \
  --fixed-fps 60 \
  --resolution 1080x1920 \
  -- --promo-mode=challenge --level=fractions_04 --duration=15
```
* **Headless/Off-Screen Constraint:** Must run inside `xvfb-run` on Linux EC2 servers or off-screen (`--position -3000,0`) on macOS.
* **Deterministic Timing:** All Promo Bot scripts must use frame delta (`_process(delta)`) or `SceneTree.create_timer(sec, false)` rather than wall-clock time (`Time.get_ticks_msec()`).

### FR-G2: In-App Promo Bot (`PromoBot.gd`)
Each Godot project includes an excluded-from-export debug controller (`res://promo/promo_bot.gd`) triggered via `--promo-mode`:
* Simulates smooth virtual pointer/touch curves with a visible kawaii hand/paw cursor asset.
* Auto-exits cleanly (`get_tree().quit(0)`) once the target demo sequence finishes.

### FR-G3: Post-Normalisation & Inbox Handoff
Trims the first `0.5s`, compresses video/audio to `H.264 + AAC` (`1080x1920`, `60fps`), and emits a sidecar manifest into `/inbox`:
```json
{
  "app_id": "kawaii_keys",
  "app_display_name": "Kawaii Keys",
  "template": "musical_showcase",
  "clip_path": "/inbox/kawaii_keys_twinkle.mp4",
  "duration_sec": 18.5,
  "preserve_game_audio": true,
  "context_summary": "Auto-playing Twinkle Twinkle Little Star with falling rainbow notes.",
  "cta_store_badges": ["App Store", "Google Play"]
}
```

---

## 4. App-Specific Promo Templates (All 8 Apps)

| # | App Name | Promo Template | Godot PromoBot Auto-Play Behavior | Audio & Overlay Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Kawaii Stories** | `story_cliffhanger` | Auto-advances through 3–4 illustrated chapter pages with character animations. | Piper TTS narrates opening scene; cuts at climax to *"Find out what happens next!"* |
| **2** | **Kawaii Math** | `timed_challenge` | Loads puzzle, hovers wrong answer, pauses `3s` before tapping correct answer, triggers confetti. | Adds `3... 2... 1...` countdown sticker during pause; hook: *"Can your 6-year-old solve this?"* |
| **3** | **Kawaii Code** | `logic_execution` | Drags visual code blocks (`Move`, `Loop`) into sequence, hits **Play** to run character through maze. | Speed-ramps block-building (`1.5x`), plays execution at `1.0x` with educational voiceover. |
| **4** | **Kawaii IQ** | `brain_teaser` | Demonstrates pattern-matching or shadow-finding puzzles with cursor movements. | Top/bottom layout: Top hook *"90% of adults fail this kids' puzzle"* + gameplay below. |
| **5** | **Kawaii Lab** | `experiment_wow` | Mixes two virtual ingredients, triggering a colorful reaction (bubbles, rainbow eruption). | High-energy sound design emphasizing safe, mess-free STEM curiosity. |
| **6** | **Kawaii Draw** | `timelapse_reveal` | Replays recorded stroke vectors step-by-step to reveal a cute kawaii character. | Speed-ramps drawing to `4x` synced to upbeat lo-fi background music. |
| **7** | **Kawaii Keys (Piano)** | `musical_showcase` | Feeds MIDI array into virtual piano keys, lighting up falling note guides with audio. | **`preserve_game_audio: true`** — mutes BGM, retains crisp piano melody; voiceover only at intro/outro. |
| **8** | **Kawaii Frets (Guitar)** | `musical_showcase` | Auto-strums chord progressions and melodies with animated chord diagrams. | **`preserve_game_audio: true`** — showcases guitar audio with caption: *"First guitar chords made fun."* |

---

## 5. Multi-Provider LLM Router & Fallback Specifications

### FR-LLM1: Three-Tier Backend Configuration
1. **Groq Cloud (`groq`):** `https://api.groq.com/openai/v1` (Model: `llama-3.3-70b-versatile`)
2. **Google AI Studio (`aistudio`):** `https://generativelanguage.googleapis.com/v1beta/openai/` (Model: `gemini-2.5-flash`)
3. **Local Ollama (`local`):** `http://127.0.0.1:11434/v1` (Model: `qwen2.5:7b` / `3b`)

### FR-LLM2: Operational Modes (`LLM_ROUTING_MODE`)
* **`manual`:** Locks requests to `LLM_MANUAL_PROVIDER` (`groq`, `aistudio`, or `local`).
* **`auto`:** Follows `LLM_FALLBACK_ORDER=groq,aistudio,local`. Triggers failover automatically on HTTP `429`, `5xx`, timeouts (`>15s`), or malformed JSON, applying a 60-second cooldown on rate-limited providers.

---

## 6. Configuration Specification (`.env`)

```env
# --- Routing Mode & Fallback ---
LLM_ROUTING_MODE=auto
LLM_MANUAL_PROVIDER=groq
LLM_FALLBACK_ORDER=groq,aistudio,local

# --- Provider Credentials ---
GROQ_API_KEY=gsk_your_groq_key_here
GEMINI_API_KEY=AIza_your_google_aistudio_key_here
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1

# --- OpenMontage Bridge ---
OPENAI_API_KEY=local-router-key
OPENAI_BASE_URL=http://127.0.0.1:4000/v1

# --- Social & Media APIs ---
PEXELS_API_KEY=your_free_pexels_key
YOUTUBE_CLIENT_SECRET_PATH=./secrets/yt_client.json
META_ACCESS_TOKEN=your_meta_graph_token
TIKTOK_ACCESS_TOKEN=your_tiktok_token
```

---

## 7. Infrastructure Sizing (AWS EC2)
* **Cloud-First + Backup Local (`c7g.xlarge` or `t4g.xlarge`):** ~`$0.12/hr`. Runs `llama3.2:3b` locally as a fallback while relying primarily on Groq & AI Studio free tiers.
* **Full Local GPU (`g4dn.xlarge` with NVIDIA T4 16GB):** ~`$0.53/hr`. Runs `qwen2.5:7b/14b` locally at high speed with zero external API dependency.


## 8. Notes
**Kawaii Apps location:** 
 /Users/macos/Documents/projects/daian/games/kawaii-<app_id>

 Example:
 /Users/macos/Documents/projects/daian/games/kawaii-<stories>
