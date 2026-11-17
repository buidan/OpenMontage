
```markdown
# Product Requirements Document (PRD)
## Automated AI Video Production & Multi-Platform Publishing Pipeline

**Version:** 2.0  
**Date:** November 13, 2026  
**Architecture Core:** OpenMontage + Multi-Provider LLM Fallback Gateway + Automated Social Distribution  

---

## 1. Executive Summary
This system automates the end-to-end creation and distribution of short-form (`9:16`) and long-form (`16:9`) social media videos for **YouTube, Facebook, Instagram Reels, and TikTok**. 

To eliminate recurring SaaS subscriptions and prevent pipeline failures caused by free-tier rate limits (`HTTP 429`) or API outages, the system implements a **Hybrid Multi-Provider LLM Router** supporting **Groq Cloud**, **Google AI Studio (`aistudio.google.com`)**, and **Local Ollama**, with both **manual override controls** and **automatic failover**.

---

## 2. System Architecture & OpenMontage Integration

### 2.1 How OpenMontage Fits In
* **OpenMontage Role:** Serves as the core deterministic production engine (`research -> proposal -> script -> scene_plan -> assets -> edit -> compose`) using Remotion (React), FFmpeg, Piper TTS, and stock media connectors (Pexels/Wikimedia).
* **Gateway Pattern (Why Needed):** OpenMontage's native `config.yaml` configures one active LLM provider at a time. To support Groq Cloud (via OpenAI-compatible endpoint), Google AI Studio, and automatic failover without modifying OpenMontage's core files, the system routes OpenMontage's `openai` provider driver to a local **LLM Router/Proxy** (`http://127.0.0.1:4000/v1`).

### 2.2 High-Level Data Flow
```text
[Trigger: Cron / Webhook / Topic Queue]
                 │
                 ▼
┌─────────────────────────────────────────┐
│       OpenMontage Pipeline Engine       │
│  (Research -> Script -> Scene Manifest) │
└────────────────┬────────────────────────┘
                 │ (OpenAI-Compatible API Call)
                 ▼
┌─────────────────────────────────────────┐
│     LLM Router & Fallback Controller    │
│  Mode: Manual Lock OR Auto-Fallback     │
│  1. Groq Cloud (Llama 3.3 70B / Qwen)   │
│  2. Google AI Studio (Gemini Flash)     │
│  3. Local Ollama (Offline Fallback)     │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│      Media Synthesis & Composition      │
│  • Voice: Piper TTS (Free Offline)      │
│  • Stock B-Roll: Pexels / Wikimedia     │
│  • Render: Remotion + FFmpeg (.mp4)     │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│    Multi-Platform Publisher Service     │
│  • YouTube Data API v3 (Shorts/Video)   │
│  • Meta Graph API (FB Reels + IG Reels) │
│  • TikTok Content Posting API           │
└─────────────────────────────────────────┘

```

---

## 3. Functional Requirements

### FR-1: Multi-Provider LLM Routing & Fallback Engine

The system must support three distinct LLM inference backends configurable via `.env` and `llm_router_config.yaml`:

1. **Groq Cloud (`groq`):**
* **Endpoint:** `https://api.groq.com/openai/v1`
* **Auth:** `GROQ_API_KEY` (`gsk_...`)
* **Target Models:** `llama-3.3-70b-versatile` (script/creative), `llama-3.1-8b-instant` (fast JSON/scene formatting).


2. **Google AI Studio (`aistudio`):**
* **Endpoint:** `https://generativelanguage.googleapis.com/v1beta/openai/`
* **Auth:** `GEMINI_API_KEY` (`AIza...`)
* **Target Models:** `gemini-2.5-flash` / `gemini-2.5-pro`.


3. **Local Inference (`local`):**
* **Endpoint:** `http://127.0.0.1:11434/v1` (Ollama)
* **Auth:** None required (`OLLAMA_API_KEY=ollama`).
* **Target Models:** `qwen2.5:7b` or `llama3.2:3b` (CPU/small GPU) / `qwen2.5:14b` (T4 GPU).



### FR-2: Manual Selection vs. Automatic Fallback Modes

The router must support two operational modes controlled by `LLM_ROUTING_MODE` in `.env`:

* **Mode 1: `manual` (Pinned Provider)**
* Forces all requests to use `LLM_MANUAL_PROVIDER` (`groq` | `aistudio` | `local`).
* Fails explicitly if the pinned provider is unreachable (useful for testing or benchmarking specific models).


* **Mode 2: `auto` (Priority Fallback Chain)**
* Follows a user-defined priority order (e.g., `LLM_FALLBACK_ORDER=groq,aistudio,local`).
* **Trigger Conditions for Failover:**
* HTTP `429` (Rate Limit / Free Tier Quota Exceeded)
* HTTP `500`, `502`, `503`, `504` (Provider Server Error)
* Request Timeout (`> 15s` for cloud APIs, `> 45s` for local Ollama)
* Malformed/non-JSON response when structured scene manifests are requested.


* **Cooldown Logic:** When a cloud provider triggers a `429` rate limit, mark it unhealthy for `60 seconds` so subsequent steps in the same video pipeline route directly to the next provider without wasting retry latency.



### FR-3: Automated Video Production (OpenMontage)

* **Input:** Topic prompt, target aspect ratio (`9:16` at `1080x1920` or `16:9` at `1920x1080`), duration target (`30s–60s` for Shorts/Reels, `3m–8m` for YouTube), and pipeline template (`explainer`, `listicle`, `news_recap`).
* **Zero-Subscription Media Pipeline:**
* **Narration:** Local **Piper TTS** (zero cost, offline).
* **Visual Assets:** Automated keyword search via **Pexels API** (free tier) and **Wikimedia Commons**, with dynamic Ken Burns pan/zoom and burned-in word-by-word captions rendered via **Remotion**.
* **Background Audio:** Royalty-free local audio library with automatic `-18dB` audio ducking under voiceover.



### FR-4: Automated Multi-Platform Distribution

Upon successful `.mp4` render and QA check (verifying file size `> 1MB`, valid audio stream, and non-black video frames):

* Generate platform-specific titles, descriptions, and hashtags via the active LLM.
* Upload via official APIs (or self-hosted scheduler webhook):
* **YouTube:** YouTube Data API v3 (`#Shorts` tag for `< 60s` vertical videos).
* **Facebook & Instagram:** Meta Graph API (publishes to Facebook Page Reels and linked Instagram Business Reels).
* **TikTok:** TikTok Content Posting API (`DIRECT_POST` or `INBOX_DRAFT` mode).


* Log video ID, provider used, fallback events, and post URLs to a local SQLite/PostgreSQL database.

---

## 4. Configuration Specification (`.env` & Router Config)

### 4.1 Environment Variables (`.env`)

```env
# --- Routing Mode: "auto" (fallback chain) or "manual" (locked provider) ---
LLM_ROUTING_MODE=auto
LLM_MANUAL_PROVIDER=groq
LLM_FALLBACK_ORDER=groq,aistudio,local

# --- Provider 1: Groq Cloud ---
GROQ_API_KEY=gsk_your_groq_key_here
GROQ_MODEL=llama-3.3-70b-versatile

# --- Provider 2: Google AI Studio ---
GEMINI_API_KEY=AIza_your_google_aistudio_key_here
GEMINI_MODEL=gemini-2.5-flash

# --- Provider 3: Local Ollama ---
OLLAMA_BASE_URL=[http://127.0.0.1:11434/v1](http://127.0.0.1:11434/v1)
OLLAMA_MODEL=qwen2.5:7b

# --- OpenMontage Bridge (Points OpenMontage to Local Router) ---
OPENAI_API_KEY=local-router-key
OPENAI_BASE_URL=[http://127.0.0.1:4000/v1](http://127.0.0.1:4000/v1)

# --- Free Media & Social APIs ---
PEXELS_API_KEY=your_free_pexels_key
YOUTUBE_CLIENT_SECRET_PATH=./secrets/yt_client.json
META_ACCESS_TOKEN=your_meta_graph_token
TIKTOK_ACCESS_TOKEN=your_tiktok_token

```

### 4.2 OpenMontage `config.yaml` Alignment

```yaml
llm:
  provider: openai          # Routes through local LLM Router via OPENAI_BASE_URL
  model: auto-router        # Resolved dynamically by the router
  temperature: 0.7
  max_tokens: 4096

budget:
  mode: cap                 # Enforces hard spend safety
  max_usd_per_video: 0.05

policy: auto_noncreative    # Runs unattended without waiting for human approval
output:
  default_format: mp4
  default_codec: libx264
  default_audio_codec: aac
  default_resolution: "1080x1920"
  default_fps: 30

```

---

## 5. Infrastructure & EC2 Deployment Sizing

| Deployment Profile | Recommended AWS EC2 Instance | Local Ollama Behavior | Best For |
| --- | --- | --- | --- |
| **Cloud-First + Emergency Local Fallback** | **`c7g.xlarge`** or **`t4g.xlarge`** (4 vCPU ARM64, 8–16 GB RAM, CPU-only) | Runs `qwen2.5:3b` or `llama3.2:3b` slowly (~10–15 tok/s) strictly as a backup if both Groq and AI Studio hit rate limits. | **Lowest hosting cost (~$0.10–$0.14/hr)**. Relies primarily on fast free tiers of Groq & AI Studio. |
| **Full Local + Cloud Burst** | **`g4dn.xlarge`** (4 vCPU, 16 GB RAM, NVIDIA T4 16GB GPU) | Runs `qwen2.5:7b` / `14b` at high speed (~45+ tok/s) with zero external dependencies. | Running `LLM_MANUAL_PROVIDER=local` at scale without rate limits. |

---

## 6. Acceptance Criteria

1. **Manual Switching:** Changing `LLM_ROUTING_MODE=manual` and `LLM_MANUAL_PROVIDER=aistudio` routes 100% of OpenMontage script and scene calls to Google AI Studio without restarting the host OS.
2. **Automatic Failover:** Simulating an invalid `GROQ_API_KEY` or `429` rate limit during an active video build automatically falls over to Google AI Studio within `< 2 seconds`, and falls back to Local Ollama if both cloud keys fail.
3. **End-to-End Unattended Run:** A single CLI trigger (`python run_pipeline.py --topic "..." --platforms youtube,facebook`) produces a rendered `1080x1920` MP4 with synchronized Piper TTS voiceover, captions, and stock footage, and posts it successfully.

```