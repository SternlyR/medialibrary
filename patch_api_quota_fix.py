#!/usr/bin/env python3
"""Patch: replace search.list (100 units) with playlistItems.list (1 unit).

Total cost drops from 100 units/refresh to 2 units/refresh.
At 15-min intervals: 192 units/day vs 9,600 units/day.
"""

import os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")

API_PY = '''\
"""YouTube Data API v3 client for fetching Shorts data."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx


@dataclass
class ShortVideo:
    video_id: str
    title: str
    description: str
    published_at: Optional[datetime]
    thumbnail_url: str
    duration_seconds: int
    views: int
    likes: int
    comments: int


class YouTubeAPI:
    BASE = "https://www.googleapis.com/youtube/v3"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def get_channel_shorts(self, channel_id: str, max_results: int = 50) -> list[ShortVideo]:
        video_ids = await self._get_recent_uploads(channel_id, max_results)
        if not video_ids:
            return []
        videos = await self._fetch_details(video_ids)
        shorts = [v for v in videos if v.duration_seconds <= 180]
        shorts.sort(key=lambda v: v.views, reverse=True)
        return shorts

    async def _get_recent_uploads(self, channel_id: str, max_results: int) -> list[str]:
        # Uploads playlist ID: swap "UC" prefix for "UU" — no extra API call needed
        uploads_playlist = "UU" + channel_id[2:]
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        video_ids: list[str] = []
        page_token: Optional[str] = None

        async with httpx.AsyncClient(timeout=30) as client:
            while len(video_ids) < max_results:
                params: dict = {
                    "part": "snippet,contentDetails",
                    "playlistId": uploads_playlist,
                    "maxResults": 50,
                    "key": self.api_key,
                }
                if page_token:
                    params["pageToken"] = page_token

                r = await client.get(f"{self.BASE}/playlistItems", params=params)
                r.raise_for_status()
                data = r.json()

                stop = False
                for item in data.get("items", []):
                    published_raw = item.get("snippet", {}).get("publishedAt", "")
                    try:
                        pub = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                        if pub < cutoff:
                            stop = True
                            break
                    except ValueError:
                        pass
                    vid_id = item.get("contentDetails", {}).get("videoId")
                    if vid_id:
                        video_ids.append(vid_id)

                page_token = data.get("nextPageToken")
                if stop or not page_token:
                    break

        return video_ids[:max_results]

    async def _fetch_details(self, video_ids: list[str]) -> list[ShortVideo]:
        results = []
        for chunk_start in range(0, len(video_ids), 50):
            chunk = video_ids[chunk_start:chunk_start + 50]
            params = {
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(chunk),
                "key": self.api_key,
            }
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(f"{self.BASE}/videos", params=params)
                r.raise_for_status()
                data = r.json()
            for item in data.get("items", []):
                parsed = self._parse(item)
                if parsed:
                    results.append(parsed)
        return results

    def _parse(self, item: dict) -> Optional[ShortVideo]:
        try:
            vid_id = item["id"]
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            details = item.get("contentDetails", {})

            duration_str = details.get("duration", "PT0S")
            duration_secs = self._iso_duration_to_seconds(duration_str)

            thumbs = snippet.get("thumbnails", {})
            thumb = (
                thumbs.get("maxres") or thumbs.get("high") or
                thumbs.get("medium") or thumbs.get("default") or {}
            )

            published_raw = snippet.get("publishedAt")
            published_at = None
            if published_raw:
                try:
                    published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                except ValueError:
                    pass

            return ShortVideo(
                video_id=vid_id,
                title=snippet.get("title", ""),
                description=snippet.get("description", ""),
                published_at=published_at,
                thumbnail_url=thumb.get("url", ""),
                duration_seconds=duration_secs,
                views=int(stats.get("viewCount", 0)),
                likes=int(stats.get("likeCount", 0)),
                comments=int(stats.get("commentCount", 0)),
            )
        except (KeyError, ValueError):
            return None

    @staticmethod
    def _iso_duration_to_seconds(duration: str) -> int:
        match = re.match(r"PT(?:(\\d+)H)?(?:(\\d+)M)?(?:(\\d+)S)?", duration)
        if not match:
            return 0
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds
'''

dest = os.path.join(ROOT, "dashboards/youtube/api.py")
with open(dest, "w") as f:
    f.write(API_PY)
print("  wrote dashboards/youtube/api.py")

subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit",
     "-m", "fix: replace search.list (100 units) with playlistItems.list (1 unit)"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done.")
