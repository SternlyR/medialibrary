"""YouTube Data API v3 client for the Shorts dashboard.

Uses only the YouTube Data API (API key only, no OAuth required).

Quota usage per refresh cycle (worst case, 50 shorts):
  - search.list         → 100 units   (find recent shorts)
  - videos.list (50)    →   1 unit     (stats + duration)
  Total: ~101 units / refresh
  At 15-min intervals: ~9696 units/day  (within 10 000 free quota)
  At 30-min intervals: ~4848 units/day  (very safe)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx


_BASE = "https://www.googleapis.com/youtube/v3"
_ISO_DURATION = re.compile(
    r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", re.IGNORECASE
)


def _parse_iso_duration(duration: str) -> int:
    """Convert ISO 8601 duration (PT1M30S) to total seconds."""
    m = _ISO_DURATION.match(duration or "")
    if not m:
        return 0
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


@dataclass
class ShortVideo:
    video_id: str
    title: str
    description: str
    published_at: datetime
    thumbnail_url: str       # best available thumbnail (landscape from API)
    duration_seconds: int
    views: int
    likes: int
    comments: int


class YouTubeAPIError(Exception):
    pass


class YouTubeAPI:
    def __init__(self, api_key: str):
        if not api_key:
            raise YouTubeAPIError("YOUTUBE_API_KEY is not configured")
        self._key = api_key
        self._client = httpx.AsyncClient(timeout=20.0)

    # ── Public helpers ─────────────────────────────────────────────────────────

    async def get_channel_shorts(
        self,
        channel_id: str,
        max_results: int = 50,
    ) -> list[ShortVideo]:
        """Return the most-viewed Shorts from *channel_id*.

        Strategy:
        1. search.list  →  recent uploads (videoDuration=short, ≤ 4 min)
        2. videos.list  →  exact duration + stats for each candidate
        3. Filter to videos with duration ≤ 60 s (true Shorts)
        4. Sort by views desc
        """
        candidates = await self._search_short_videos(channel_id, max_results)
        if not candidates:
            return []

        detailed = await self._get_video_details(candidates)
        shorts = [v for v in detailed if v.duration_seconds <= 60]
        shorts.sort(key=lambda v: v.views, reverse=True)
        return shorts

    async def get_7day_metrics(
        self, channel_id: str
    ) -> dict[str, int]:
        """Return summed views/likes/comments for Shorts published in last 7 days.

        Note: This sums *total* stats on videos published within 7 days.
        True incremental analytics require the YouTube Analytics API (OAuth).
        """
        from datetime import timedelta

        shorts = await self.get_channel_shorts(channel_id, max_results=50)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        recent = [
            s for s in shorts
            if s.published_at and s.published_at >= cutoff
        ]

        return {
            "views_7d": sum(s.views for s in recent),
            "likes_7d": sum(s.likes for s in recent),
            "comments_7d": sum(s.comments for s in recent),
        }

    async def close(self) -> None:
        await self._client.aclose()

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _search_short_videos(
        self, channel_id: str, max_results: int
    ) -> list[str]:
        """Return list of video IDs (search.list, 100 quota units)."""
        params = {
            "part": "id",
            "channelId": channel_id,
            "type": "video",
            "videoDuration": "short",   # < 4 minutes
            "order": "viewCount",
            "maxResults": min(max_results, 50),
            "key": self._key,
        }
        resp = await self._client.get(f"{_BASE}/search", params=params)
        self._raise_for_status(resp)
        data = resp.json()
        return [item["id"]["videoId"] for item in data.get("items", [])]

    async def _get_video_details(self, video_ids: list[str]) -> list[ShortVideo]:
        """Fetch contentDetails + statistics for up to 50 videos (1 quota unit)."""
        results: list[ShortVideo] = []
        # API allows max 50 ids per call
        for chunk in _chunks(video_ids, 50):
            params = {
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(chunk),
                "key": self._key,
            }
            resp = await self._client.get(f"{_BASE}/videos", params=params)
            self._raise_for_status(resp)
            data = resp.json()
            for item in data.get("items", []):
                vid = self._parse_video_item(item)
                if vid:
                    results.append(vid)
        return results

    def _parse_video_item(self, item: dict) -> Optional[ShortVideo]:
        try:
            snippet = item["snippet"]
            details = item["contentDetails"]
            stats = item.get("statistics", {})

            thumbnails = snippet.get("thumbnails", {})
            thumb = (
                thumbnails.get("maxres")
                or thumbnails.get("standard")
                or thumbnails.get("high")
                or thumbnails.get("medium")
                or thumbnails.get("default")
                or {}
            )

            published_raw = snippet.get("publishedAt", "")
            try:
                published_at = datetime.fromisoformat(
                    published_raw.replace("Z", "+00:00")
                )
            except ValueError:
                published_at = datetime.now(timezone.utc)

            return ShortVideo(
                video_id=item["id"],
                title=snippet.get("title", ""),
                description=snippet.get("description", ""),
                published_at=published_at,
                thumbnail_url=thumb.get("url", ""),
                duration_seconds=_parse_iso_duration(
                    details.get("duration", "PT0S")
                ),
                views=int(stats.get("viewCount", 0)),
                likes=int(stats.get("likeCount", 0)),
                comments=int(stats.get("commentCount", 0)),
            )
        except (KeyError, ValueError, TypeError):
            return None

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code != 200:
            try:
                msg = resp.json().get("error", {}).get("message", resp.text)
            except Exception:
                msg = resp.text
            raise YouTubeAPIError(
                f"YouTube API error {resp.status_code}: {msg}"
            )


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]
