"""TikTok Content Posting API (FILE_UPLOAD), DIRECT_POST or INBOX_DRAFT.

Env:
  TIKTOK_ACCESS_TOKEN   user access token with video.publish (direct) / video.upload (inbox)
  TIKTOK_POST_MODE      DIRECT_POST (default) | INBOX_DRAFT
  TIKTOK_PRIVACY_LEVEL  default SELF_ONLY — apps that have not passed TikTok's
                        audit can only post SELF_ONLY; set PUBLIC_TO_EVERYONE after approval.
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

import requests

from autopilot.publishers import PublishError, PublishResult, check_response

API = "https://open.tiktokapis.com/v2"
SINGLE_CHUNK_MAX = 64 * 1024 * 1024
CHUNK = 10 * 1024 * 1024


def _headers() -> dict[str, str]:
    token = os.environ.get("TIKTOK_ACCESS_TOKEN", "").strip()
    if not token:
        raise PublishError("TIKTOK_ACCESS_TOKEN is not set")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}


def _api(path: str, body: dict) -> dict:
    data = check_response(requests.post(f"{API}{path}", json=body, headers=_headers(), timeout=60), f"TikTok {path}")
    err = data.get("error") or {}
    if err.get("code") not in (None, "ok"):
        raise PublishError(f"TikTok {path}: {err.get('code')} {err.get('message')}")
    return data.get("data") or {}


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count). The final chunk absorbs the remainder."""
    if size <= SINGLE_CHUNK_MAX:
        return size, 1
    return CHUNK, max(1, math.floor(size / CHUNK))


def publish(video: Path, metadata: dict, aspect: str, seconds: float) -> PublishResult:
    mode = os.environ.get("TIKTOK_POST_MODE", "DIRECT_POST").upper()
    size = video.stat().st_size
    chunk_size, count = chunk_plan(size)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": count}

    username = None
    if mode == "DIRECT_POST":
        creator = _api("/post/publish/creator_info/query/", {})
        username = creator.get("creator_username")
        options = creator.get("privacy_level_options") or []
        privacy = os.environ.get("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY")
        if options and privacy not in options:
            raise PublishError(f"TIKTOK_PRIVACY_LEVEL={privacy} not allowed for this account; options: {options}")
        max_sec = creator.get("max_video_post_duration_sec")
        if max_sec and seconds > max_sec:
            raise PublishError(f"video is {seconds:.0f}s; this account allows {max_sec}s")
        init = _api("/post/publish/video/init/", {
            "post_info": {
                "title": metadata["tiktok"]["title"], "privacy_level": privacy,
                "disable_duet": False, "disable_comment": False, "disable_stitch": False,
                "is_aigc": os.environ.get("TIKTOK_IS_AIGC", "true").lower() == "true",
            },
            "source_info": source,
        })
    elif mode == "INBOX_DRAFT":
        init = _api("/post/publish/inbox/video/init/", {"source_info": source})
    else:
        raise PublishError(f"unknown TIKTOK_POST_MODE {mode!r}")

    publish_id, upload_url = init.get("publish_id"), init.get("upload_url")
    if not publish_id or not upload_url:
        raise PublishError(f"TikTok init returned no upload target: {init}")

    with open(video, "rb") as f:
        for i in range(count):
            first = i * chunk_size
            last = size - 1 if i == count - 1 else first + chunk_size - 1
            f.seek(first)
            body = f.read(last - first + 1)
            resp = requests.put(upload_url, data=body, timeout=600, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(body)),
                "Content-Range": f"bytes {first}-{last}/{size}",
            })
            if resp.status_code not in (200, 201, 206):
                raise PublishError(f"TikTok chunk {i + 1}/{count} upload failed: HTTP {resp.status_code} {resp.text[:300]}")

    deadline = time.time() + float(os.environ.get("TIKTOK_PROCESSING_TIMEOUT", "600"))
    status, data = "", {}
    while time.time() < deadline:
        data = _api("/post/publish/status/fetch/", {"publish_id": publish_id})
        status = data.get("status", "")
        if status in {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"}:
            break
        if status == "FAILED":
            raise PublishError(f"TikTok processing failed: {data.get('fail_reason')}")
        time.sleep(8)
    if status not in {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"}:
        raise PublishError(f"TikTok processing did not finish in time (last status {status!r})")

    if status == "SEND_TO_USER_INBOX":
        return PublishResult("tiktok", "draft", publish_id, None, detail={"note": "open TikTok inbox to finish posting"})
    # (sic) TikTok's field name is spelled "publicaly".
    post_ids = data.get("publicaly_available_post_id") or []
    post_id = str(post_ids[0]) if post_ids else publish_id
    url = f"https://www.tiktok.com/@{username}/video/{post_id}" if username and post_ids else None
    return PublishResult("tiktok", "published", post_id, url,
                         detail={"privacy": os.environ.get("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY")})
