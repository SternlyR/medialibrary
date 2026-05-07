#!/usr/bin/env python3
"""Patch: channel-level total_views, 200h pruning, quota fix (playlistItems vs search.list).

Fixes:
  - total_views in snapshots now comes from channels.list?part=statistics (all-time
    cumulative channel viewCount) instead of summing 30-day Shorts video views.
    This stops the -3M drops that happen when a video ages out of the 30-day window.
  - Snapshot pruning extended from 48h to 200h so 7-day chart history is preserved.
  - Replaces search.list (100 quota units) with playlistItems.list (1 unit).
  - Clears all existing snapshots so the chart baseline resets cleanly to the true
    channel total. Backfill will rerun on next service restart.
"""

import os, re, sqlite3, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")

# ── 1. Add get_channel_view_count to api.py ────────────────────────────────────
API_PATH = os.path.join(ROOT, "dashboards/youtube/api.py")
api = open(API_PATH).read()

if "get_channel_view_count" not in api:
    api = api.replace(
        "    async def get_channel_shorts(",
        """    async def get_channel_view_count(self, channel_id: str) -> int:
        \"\"\"All-time cumulative viewCount for the channel (never drops).\"\"\"
        params = {"part": "statistics", "id": channel_id, "key": self.api_key}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{self.BASE}/channels", params=params)
            r.raise_for_status()
            data = r.json()
        items = data.get("items", [])
        if not items:
            return 0
        return int(items[0].get("statistics", {}).get("viewCount", 0))

    async def get_channel_shorts(""",
    )
    print("  added get_channel_view_count to api.py")
else:
    print("  get_channel_view_count already present — skipping")

# ── 2. Replace _search_shorts with _get_recent_uploads (quota fix) ─────────────
if "_get_recent_uploads" not in api:
    # Replace the call site first
    api = api.replace(
        "video_ids = await self._search_shorts(channel_id, max_results)",
        "video_ids = await self._get_recent_uploads(channel_id, max_results)",
    )
    # Replace the method definition
    api = re.sub(
        r'async def _search_shorts\(self[^)]+\).*?return \[item\["id"\]\["videoId"\] for item in data\.get\("items", \[\]\)\]',
        '''async def _get_recent_uploads(self, channel_id: str, max_results: int) -> list[str]:
        uploads_playlist = "UU" + channel_id[2:]  # UC→UU prefix swap, no extra API call
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        video_ids: list[str] = []
        page_token: Optional[str] = None
        async with httpx.AsyncClient(timeout=30) as client:
            while len(video_ids) < max_results:
                params = {
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
        return video_ids[:max_results]''',
        api,
        flags=re.DOTALL,
    )
    print("  replaced _search_shorts with _get_recent_uploads (1 quota unit vs 100)")
else:
    print("  _get_recent_uploads already present — skipping")

open(API_PATH, "w").write(api)

# ── 3. Patch cache.py ──────────────────────────────────────────────────────────
CACHE_PATH = os.path.join(ROOT, "dashboards/youtube/cache.py")
cache = open(CACHE_PATH).read()
changed = []

# 3a. Fix snapshot pruning: 48h → 200h
if "timedelta(hours=48)" in cache:
    cache = cache.replace(
        "cutoff = datetime.utcnow() - timedelta(hours=48)",
        "cutoff = datetime.utcnow() - timedelta(hours=200)",
    )
    changed.append("pruning 48h→200h")

# 3b. Use channel-level viewCount instead of summing video views.
#     Replace the total_views line and wrap with try/except fallback.
OLD_SNAPSHOT = "        # Snapshot total views\n        total_views = sum(s.views for s in shorts)"
NEW_SNAPSHOT = """        # Snapshot total views — channel all-time cumulative (channels.list statistics).
        # Using channel-level stat means the chart baseline never drops when videos
        # age out of the 30-day search window.
        try:
            total_views = await api.get_channel_view_count(channel_id)
            if total_views == 0:
                raise ValueError("viewCount returned 0 — falling back")
        except Exception as _exc:
            print(f"[yt-cache] channel viewCount unavailable ({_exc}), falling back to video sum")
            total_views = sum(s.views for s in shorts)"""

if "total_views = sum(s.views for s in shorts)" in cache:
    cache = cache.replace(OLD_SNAPSHOT, NEW_SNAPSHOT)
    changed.append("total_views → channel viewCount")
elif "get_channel_view_count" in cache:
    changed.append("total_views already uses get_channel_view_count — skipping")
else:
    print("  WARNING: could not find total_views line to replace — check cache.py manually")

if changed:
    open(CACHE_PATH, "w").write(cache)
    print(f"  updated cache.py: {', '.join(changed)}")

# ── 4. Clear all existing snapshots so chart resets to correct baseline ─────────
DB_PATH = os.path.join(ROOT, "insight.db")
if os.path.exists(DB_PATH):
    con = sqlite3.connect(DB_PATH)
    deleted = con.execute("DELETE FROM youtube_views_snapshots").rowcount
    con.commit()
    con.close()
    print(f"  cleared {deleted} snapshots — chart will rebuild from correct channel baseline on next restart")
else:
    print("  insight.db not found — skipping snapshot clear")

# ── 5. Commit ──────────────────────────────────────────────────────────────────
subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit",
     "-m", "fix: channel-level viewCount for snapshots, 200h pruning, quota fix"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — restart the service to pick up the new baseline.")
print("First refresh will read channels.list.statistics.viewCount as the true baseline.")
print("If backfill is configured, it will regenerate 7-day history on that new baseline.")
