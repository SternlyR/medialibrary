#!/usr/bin/env python3
"""Patch: channel-level 30-day metrics via Analytics API for the header stats.

Changes:
  - Creates analytics_api.py with OAuth token refresh + channel metrics
  - Adds google_client_id/secret/refresh_token to config.py
  - Replaces get_metrics_30d() in cache.py to call Analytics API
  - Fixes server.py chart endpoint to use hours=168 (7 days)
"""

import os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")

# ── 1. Create analytics_api.py ─────────────────────────────────────────────────
ANALYTICS_PATH = os.path.join(ROOT, "dashboards/youtube/analytics_api.py")

open(ANALYTICS_PATH, "w").write('''"""YouTube Analytics API v2 — channel-level metrics with OAuth token refresh."""

from datetime import date, timedelta
import httpx


async def _get_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post("https://oauth2.googleapis.com/token", data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        r.raise_for_status()
        return r.json()["access_token"]


class YouTubeAnalyticsAPI:
    BASE = "https://youtubeanalytics.googleapis.com/v2"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token

    async def get_channel_metrics_30d(self, channel_id: str) -> dict:
        """Returns {views, likes, comments} for the channel over the past 30 days."""
        token = await _get_access_token(self._client_id, self._client_secret, self._refresh_token)
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=29)
        params = {
            "ids": f"channel=={channel_id}",
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "metrics": "views,likes,comments",
        }
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{self.BASE}/reports", params=params, headers=headers)
            r.raise_for_status()
            data = r.json()
        rows = data.get("rows", [])
        if not rows:
            return {"views": 0, "likes": 0, "comments": 0}
        row = rows[0]
        return {"views": int(row[0]), "likes": int(row[1]), "comments": int(row[2])}

    async def get_daily_views(self, channel_id: str, days: int = 7) -> dict:
        """Returns {date_str: view_count} for the past N days."""
        token = await _get_access_token(self._client_id, self._client_secret, self._refresh_token)
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=days - 1)
        params = {
            "ids": f"channel=={channel_id}",
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "metrics": "views",
            "dimensions": "day",
            "sort": "day",
        }
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{self.BASE}/reports", params=params, headers=headers)
            r.raise_for_status()
            data = r.json()
        return {row[0]: int(row[1]) for row in data.get("rows", [])}
''')
print("  wrote analytics_api.py")

# ── 2. Add OAuth fields to config.py ──────────────────────────────────────────
CONFIG_PATH = os.path.join(ROOT, "dashboards/config.py")
config = open(CONFIG_PATH).read()

if "google_client_id" not in config:
    config = config.replace(
        "    youtube_refresh_interval_minutes: int = 15",
        """    youtube_refresh_interval_minutes: int = 15
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = \"\""""
    )
    open(CONFIG_PATH, "w").write(config)
    print("  added OAuth fields to config.py")
else:
    print("  OAuth fields already in config.py — skipping")

# ── 3. Replace get_metrics_30d() in cache.py ──────────────────────────────────
CACHE_PATH = os.path.join(ROOT, "dashboards/youtube/cache.py")
cache = open(CACHE_PATH).read()

OLD_METRICS = """async def get_metrics_30d() -> dict:
    if not _engine:
        return {"views": 0, "likes": 0, "comments": 0}
    cutoff = datetime.utcnow() - timedelta(days=30)
    async with get_session(_engine) as session:
        result = await session.execute(
            select(
                func.sum(YoutubeShort.views),
                func.sum(YoutubeShort.likes),
                func.sum(YoutubeShort.comments),
            ).where(
                YoutubeShort.channel_id == settings.youtube_channel_id,
                YoutubeShort.published_at >= cutoff,
            )
        )
        row = result.one()
    return {
        "views": row[0] or 0,
        "likes": row[1] or 0,
        "comments": row[2] or 0,
    }"""

NEW_METRICS = """async def get_metrics_30d() -> dict:
    \"\"\"Channel-level 30-day views/likes/comments from YouTube Analytics API.\"\"\"
    from dashboards.youtube.analytics_api import YouTubeAnalyticsAPI
    if not (settings.google_client_id and settings.google_refresh_token):
        print("[yt-cache] No OAuth credentials — metrics will show 0. Add GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN to .env")
        return {"views": 0, "likes": 0, "comments": 0}
    try:
        analytics = YouTubeAnalyticsAPI(
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_refresh_token,
        )
        return await analytics.get_channel_metrics_30d(settings.youtube_channel_id)
    except Exception as exc:
        print(f"[yt-cache] Analytics API error for metrics: {exc}")
        return {"views": 0, "likes": 0, "comments": 0}"""

if OLD_METRICS in cache:
    cache = cache.replace(OLD_METRICS, NEW_METRICS)
    open(CACHE_PATH, "w").write(cache)
    print("  replaced get_metrics_30d() with Analytics API version")
elif "analytics_api" in cache:
    print("  get_metrics_30d() already uses Analytics API — skipping")
else:
    print("  WARNING: could not match get_metrics_30d() exactly — check cache.py manually")
    print("  Hint: grep -n 'get_metrics_30d' ~/insight-dashboards/dashboards/youtube/cache.py")

# ── 4. Fix server.py chart endpoint: hours=24 → hours=168 ─────────────────────
SERVER_PATH = os.path.join(ROOT, "dashboards/server.py")
server = open(SERVER_PATH).read()
if "hours=24" in server:
    server = server.replace("get_chart_data(hours=24)", "get_chart_data(hours=168)")
    open(SERVER_PATH, "w").write(server)
    print("  fixed server.py: chart endpoint hours=24 → hours=168")
else:
    print("  server.py chart endpoint already uses hours=168 — skipping")

# ── 5. Commit ──────────────────────────────────────────────────────────────────
subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit",
     "-m", "feat: channel-level 30d metrics via Analytics API, chart 168h window"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")

print("\nDone — restart the service.")
print("The header stats will now show channel-level 30-day totals from YouTube Analytics.")
print("If you see zeros, check that .env has GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN.")
