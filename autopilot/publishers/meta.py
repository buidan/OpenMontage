"""Meta Graph API: Facebook Page Reels and Instagram Reels (resumable upload).

Env:
  META_ACCESS_TOKEN   long-lived *Page* access token (pages_manage_posts,
                      pages_read_engagement, instagram_basic,
                      instagram_content_publish) from Facebook Login for Business
  META_PAGE_ID        Facebook Page id
  META_IG_USER_ID     Instagram Business/Creator account id linked to the Page
  META_GRAPH_VERSION  default v25.0
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from autopilot.publishers import PublishError, PublishResult, check_response


def _cfg(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise PublishError(f"{key} is not set")
    return val


def _graph() -> str:
    return f"https://graph.facebook.com/{os.environ.get('META_GRAPH_VERSION', 'v25.0')}"


def _upload_bytes(url: str, token: str, video: Path) -> dict:
    size = video.stat().st_size
    with open(video, "rb") as f:
        resp = requests.post(url, data=f, timeout=3600, headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size),
            "Content-Type": "application/octet-stream",
        })
    return check_response(resp, "Meta byte upload")


def publish_facebook(video: Path, metadata: dict, aspect: str, seconds: float) -> PublishResult:
    token, page = _cfg("META_ACCESS_TOKEN"), _cfg("META_PAGE_ID")
    if aspect != "9:16":
        raise PublishError("Facebook Reels require a vertical 9:16 video")
    start = check_response(requests.post(f"{_graph()}/{page}/video_reels",
                                         data={"upload_phase": "start", "access_token": token}, timeout=60),
                           "Facebook reel start")
    video_id = start.get("video_id")
    upload_url = start.get("upload_url")
    if not video_id or not upload_url:
        raise PublishError(f"Facebook reel start returned no upload target: {start}")
    _upload_bytes(upload_url, token, video)
    check_response(requests.post(f"{_graph()}/{page}/video_reels", timeout=60, data={
        "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
        "description": metadata["facebook"]["description"], "access_token": token,
    }), "Facebook reel finish")
    url = None
    for _ in range(12):  # processing is async; the permalink appears once it is ready
        info = requests.get(f"{_graph()}/{video_id}", timeout=30,
                            params={"fields": "permalink_url,status", "access_token": token}).json()
        url = info.get("permalink_url")
        if url:
            url = url if url.startswith("http") else f"https://www.facebook.com{url}"
            break
        time.sleep(10)
    return PublishResult("facebook", "published", video_id, url or f"https://www.facebook.com/reel/{video_id}")


def publish_instagram(video: Path, metadata: dict, aspect: str, seconds: float) -> PublishResult:
    token, ig = _cfg("META_ACCESS_TOKEN"), _cfg("META_IG_USER_ID")
    container = check_response(requests.post(f"{_graph()}/{ig}/media", timeout=60, data={
        "media_type": "REELS", "upload_type": "resumable", "share_to_feed": "true",
        "caption": metadata["instagram"]["caption"], "access_token": token,
    }), "Instagram container create")
    cid, uri = container.get("id"), container.get("uri")
    if not cid or not uri:
        raise PublishError(f"Instagram container create returned no upload uri: {container}")
    _upload_bytes(uri, token, video)

    deadline = time.time() + float(os.environ.get("META_PROCESSING_TIMEOUT", "600"))
    status = ""
    while time.time() < deadline:
        info = check_response(requests.get(f"{_graph()}/{cid}", timeout=30,
                                           params={"fields": "status_code,status", "access_token": token}),
                              "Instagram container status")
        status = info.get("status_code", "")
        if status == "FINISHED":
            break
        if status in {"ERROR", "EXPIRED"}:
            raise PublishError(f"Instagram processing {status}: {info.get('status')}")
        time.sleep(10)
    if status != "FINISHED":
        raise PublishError(f"Instagram processing did not finish in time (last status {status!r})")

    pub = check_response(requests.post(f"{_graph()}/{ig}/media_publish", timeout=60,
                                       data={"creation_id": cid, "access_token": token}),
                         "Instagram publish")
    media_id = pub.get("id")
    link = requests.get(f"{_graph()}/{media_id}", timeout=30,
                        params={"fields": "permalink", "access_token": token}).json().get("permalink")
    return PublishResult("instagram", "published", media_id, link)
