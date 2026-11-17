"""YouTube Data API v3 upload (resumable), OAuth installed-app credentials.

One-time setup:
  1. Google Cloud console -> enable "YouTube Data API v3" -> create an OAuth
     client of type "Desktop app" -> download JSON to YOUTUBE_CLIENT_SECRET_PATH.
  2. python run_pipeline.py --authorize youtube   (opens a browser once and
     writes the refresh token to YOUTUBE_TOKEN_PATH, default .youtube-token.json)

Note: uploads from API projects that have not passed Google's audit are
forced to private regardless of YOUTUBE_PRIVACY.
"""

from __future__ import annotations

import os
from pathlib import Path

from autopilot.publishers import PublishError, PublishResult, check_response

ROOT = Path(__file__).resolve().parents[2]
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


def _path(env: str, default: str) -> Path:
    p = Path(os.environ.get(env) or default)
    return p if p.is_absolute() else ROOT / p


def authorize() -> Path:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise PublishError("pip install google-auth-oauthlib  (see requirements-autopilot.txt)") from exc
    secret = _path("YOUTUBE_CLIENT_SECRET_PATH", "secrets/yt_client.json")
    if not secret.is_file():
        raise PublishError(f"OAuth client JSON not found at {secret}")
    creds = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES).run_local_server(port=0)
    token = _path("YOUTUBE_TOKEN_PATH", ".youtube-token.json")
    token.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(token, 0o600)
    return token


def _session():
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2.credentials import Credentials

    token = _path("YOUTUBE_TOKEN_PATH", ".youtube-token.json")
    if not token.is_file():
        raise PublishError(f"no YouTube token at {token}; run: python run_pipeline.py --authorize youtube")
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        token.write_text(creds.to_json(), encoding="utf-8")
    return AuthorizedSession(creds)


def publish(video: Path, metadata: dict, aspect: str, seconds: float) -> PublishResult:
    yt = metadata["youtube"]
    privacy = os.environ.get("YOUTUBE_PRIVACY", "private")
    body = {
        "snippet": {
            "title": yt["title"],
            "description": yt["description"],
            "tags": yt["tags"],
            "categoryId": os.environ.get("YOUTUBE_CATEGORY_ID", "27"),
        },
        "status": {
            "privacyStatus": privacy,
            # Per app/template decision (promo/apps.yaml made_for_kids); topic videos default to False.
            "selfDeclaredMadeForKids": bool(yt.get("made_for_kids", False)),
            # AI-written script + synthetic voice: disclose by default (YOUTUBE_SYNTHETIC_MEDIA=false to opt out).
            "containsSyntheticMedia": os.environ.get("YOUTUBE_SYNTHETIC_MEDIA", "true").lower() == "true",
        },
    }
    size = video.stat().st_size
    session = _session()
    init = session.post(
        UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"}, json=body,
        headers={"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)}, timeout=60,
    )
    check_response(init, "YouTube upload init")
    location = init.headers.get("Location")
    if not location:
        raise PublishError("YouTube did not return a resumable upload URL")
    with open(video, "rb") as f:
        resp = session.put(location, data=f, headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
                           timeout=3600)
    data = check_response(resp, "YouTube upload")
    vid = data.get("id")
    is_short = aspect == "9:16" and seconds <= 180
    url = f"https://youtube.com/shorts/{vid}" if is_short else f"https://www.youtube.com/watch?v={vid}"
    return PublishResult("youtube", "published", vid, url, detail={"privacy": privacy})
