#!/usr/bin/env python3
"""Patch: wrap backfill in try/except so APScheduler can't swallow errors."""

import base64, os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")

CACHE_PY = b"""\"\"\"APScheduler-backed YouTube data cache with SQLite persistence.\"\"\"

from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, func, delete
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from dashboards.config import settings
from dashboards.database import YoutubeShort, YoutubeViewsSnapshot, get_session
from dashboards.youtube.api import YouTubeAPI

# Hourly viewing weight curve (UTC 00-23), calibrated to US CDT audience.
# Peak at UTC 18-23 (1PM-6PM CDT). Normalised to fractions at module load.
_HOURLY_WEIGHTS = [
    5.0, 4.5, 4.0, 3.2, 2.5, 2.0,   # UTC 00-05  (7PM-midnight CDT prior eve)
    1.7, 1.5, 1.7, 2.0, 2.5, 3.2,   # UTC 06-11  (1AM-6AM CDT)
    3.8, 4.2, 4.6, 5.0, 5.5, 6.0,   # UTC 12-17  (7AM-noon CDT, ramp)
    6.5, 6.2, 5.8, 5.5, 5.0, 4.6,   # UTC 18-23  (1PM-6PM CDT, peak)
]
_w_total = sum(_HOURLY_WEIGHTS)
_HOURLY_PCT = [w / _w_total for w in _HOURLY_WEIGHTS]

_scheduler: Optional[AsyncIOScheduler] = None
_engine = None


async def start(engine):
    global _scheduler, _engine
    _engine = engine
    _scheduler = AsyncIOScheduler()
    interval = max(5, settings.youtube_refresh_interval_minutes)
    _scheduler.add_job(
        _refresh,
        "interval",
        minutes=interval,
        next_run_time=datetime.now(),  # fire immediately on startup
        id="yt_refresh",
    )
    _scheduler.start()


async def stop():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)


async def refresh_now():
    await _refresh()


async def _refresh():
    if not _engine:
        return
    api = YouTubeAPI(settings.youtube_api_key)
    channel_id = settings.youtube_channel_id
    try:
        shorts = await api.get_channel_shorts(channel_id, max_results=50)
    except Exception as exc:
        print(f"[yt-cache] API error: {exc}")
        return

    async with get_session(_engine) as session:
        for rank, short in enumerate(shorts):
            stmt = sqlite_insert(YoutubeShort).values(
                channel_id=channel_id,
                video_id=short.video_id,
                title=short.title,
                description=short.description,
                published_at=short.published_at,
                thumbnail_url=short.thumbnail_url,
                duration_seconds=short.duration_seconds,
                views=short.views,
                likes=short.likes,
                comments=short.comments,
                rank=rank,
                updated_at=datetime.utcnow(),
            ).on_conflict_do_update(
                index_elements=["video_id"],
                set_={
                    "title": short.title,
                    "views": short.views,
                    "likes": short.likes,
                    "comments": short.comments,
                    "rank": rank,
                    "updated_at": datetime.utcnow(),
                },
            )
            await session.execute(stmt)

        total_views = sum(s.views for s in shorts)
        session.add(YoutubeViewsSnapshot(
            channel_id=channel_id,
            timestamp=datetime.utcnow(),
            total_views=total_views,
        ))

        cutoff = datetime.utcnow() - timedelta(hours=170)
        await session.execute(
            delete(YoutubeViewsSnapshot).where(YoutubeViewsSnapshot.timestamp < cutoff)
        )
        await session.commit()

    print(f"[yt-cache] Refreshed {len(shorts)} shorts, total_views={total_views:,}")

    # Wrap backfill so APScheduler cannot silently swallow exceptions
    try:
        await _backfill_if_needed()
    except Exception as exc:
        import traceback
        print(f"[yt-backfill] UNHANDLED ERROR: {exc}")
        traceback.print_exc()


async def _backfill_if_needed():
    \"\"\"On startup with sparse history, synthesize 7 days of hourly snapshots.\"\"\"
    if not (settings.youtube_client_id and settings.youtube_client_secret
            and settings.youtube_refresh_token):
        print("[yt-backfill] Skipping: OAuth credentials not configured")
        return

    channel_id = settings.youtube_channel_id
    cutoff_24h = datetime.utcnow() - timedelta(hours=24)

    async with get_session(_engine) as session:
        result = await session.execute(
            select(func.count(YoutubeViewsSnapshot.id)).where(
                YoutubeViewsSnapshot.channel_id == channel_id,
                YoutubeViewsSnapshot.timestamp < cutoff_24h,
            )
        )
        old_count = result.scalar_one()
        if old_count > 0:
            return  # Already have data older than 24h — backfill already ran

        result = await session.execute(
            select(func.max(YoutubeViewsSnapshot.total_views)).where(
                YoutubeViewsSnapshot.channel_id == channel_id,
            )
        )
        current_total = result.scalar_one_or_none() or 0

    if not current_total:
        print("[yt-backfill] Skipping: no current snapshot total found")
        return

    print(f"[yt-backfill] Starting — current_total={current_total:,}")

    from dashboards.youtube.analytics_api import YouTubeAnalyticsAPI
    api = YouTubeAnalyticsAPI(
        settings.youtube_client_id,
        settings.youtube_client_secret,
        settings.youtube_refresh_token,
    )
    try:
        daily_views = await api.get_daily_views(days=8)
    except Exception as exc:
        print(f"[yt-backfill] Analytics API error: {exc}")
        return

    if not daily_views:
        print("[yt-backfill] Skipping: Analytics API returned no rows")
        return

    print(f"[yt-backfill] Got {len(daily_views)} days from Analytics API: {list(daily_views.keys())}")

    now = datetime.utcnow()
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hourly_views: list[tuple[datetime, float]] = []
    for days_ago in range(7, -1, -1):
        day_start = today_midnight - timedelta(days=days_ago)
        day_total = daily_views.get(day_start.strftime("%Y-%m-%d"), 0)
        for h in range(24):
            ts = day_start + timedelta(hours=h)
            if ts >= now:
                break
            hourly_views.append((ts, day_total * _HOURLY_PCT[h]))

    print(f"[yt-backfill] Built {len(hourly_views)} hourly estimates, computing cumulative totals...")

    synthetic: list[tuple[datetime, int]] = []
    running = float(current_total)
    for ts, h_views in reversed(hourly_views):
        synthetic.append((ts, max(0, int(running))))
        running -= h_views
    synthetic.reverse()

    print(f"[yt-backfill] Inserting up to {len(synthetic)} synthetic snapshots...")

    inserted = 0
    async with get_session(_engine) as session:
        for ts, total in synthetic:
            lo = ts - timedelta(minutes=30)
            hi = ts + timedelta(minutes=30)
            exists = await session.execute(
                select(YoutubeViewsSnapshot.id).where(
                    YoutubeViewsSnapshot.channel_id == channel_id,
                    YoutubeViewsSnapshot.timestamp >= lo,
                    YoutubeViewsSnapshot.timestamp <= hi,
                ).limit(1)
            )
            if exists.scalar_one_or_none() is None:
                session.add(YoutubeViewsSnapshot(
                    channel_id=channel_id,
                    timestamp=ts,
                    total_views=total,
                ))
                inserted += 1
        await session.commit()

    print(f"[yt-backfill] Inserted {inserted} synthetic hourly snapshots")


async def get_shorts(limit: int = 9) -> list[dict]:
    if not _engine:
        return []
    cutoff_30d = datetime.utcnow() - timedelta(days=30)
    async with get_session(_engine) as session:
        result = await session.execute(
            select(YoutubeShort)
            .where(
                YoutubeShort.channel_id == settings.youtube_channel_id,
                YoutubeShort.published_at >= cutoff_30d,
            )
            .order_by(YoutubeShort.rank)
            .limit(limit)
        )
        rows = result.scalars().all()
    return [
        {
            "video_id": r.video_id,
            "title": r.title,
            "thumbnail_url": r.thumbnail_url,
            "views": r.views,
            "likes": r.likes,
            "comments": r.comments,
            "published_at": r.published_at.isoformat() if r.published_at else None,
        }
        for r in rows
    ]


async def get_metrics_30d() -> dict:
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
    }


async def get_chart_data(hours: int = 168) -> dict:
    \"\"\"Hourly view-delta bars for the last N hours. Spikes filtered.\"\"\"
    if not _engine:
        return {"labels": [], "values": []}

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    async with get_session(_engine) as session:
        result = await session.execute(
            select(YoutubeViewsSnapshot)
            .where(
                YoutubeViewsSnapshot.channel_id == settings.youtube_channel_id,
                YoutubeViewsSnapshot.timestamp >= cutoff,
            )
            .order_by(YoutubeViewsSnapshot.timestamp)
        )
        snaps = result.scalars().all()

    if len(snaps) < 2:
        return {"labels": [], "values": []}

    buckets: dict = {}
    for snap in snaps:
        hour = snap.timestamp.replace(minute=0, second=0, microsecond=0)
        if hour not in buckets or snap.total_views > buckets[hour].total_views:
            buckets[hour] = snap

    sorted_hours = sorted(buckets.keys())
    if len(sorted_hours) < 2:
        return {"labels": [], "values": []}

    pairs = []
    for i in range(1, len(sorted_hours)):
        prev_h, curr_h = sorted_hours[i - 1], sorted_hours[i]
        if (curr_h - prev_h).total_seconds() > 7200:
            continue
        delta = max(0, buckets[curr_h].total_views - buckets[prev_h].total_views)
        pairs.append((curr_h, delta))

    if not pairs:
        return {"labels": [], "values": []}

    sorted_vals = sorted(d for _, d in pairs)
    median = sorted_vals[len(sorted_vals) // 2]
    spike_threshold = max(median * 10, 1)

    labels, values = [], []
    for h, d in pairs:
        labels.append(h.strftime("%m/%d %H:00") if hours > 48 else f"{h.hour:02d}:00")
        values.append(0 if d > spike_threshold else d)

    return {"labels": labels, "values": values}
"""

dest = os.path.join(ROOT, "dashboards/youtube/cache.py")
with open(dest, "wb") as f:
    f.write(CACHE_PY)
print("  wrote dashboards/youtube/cache.py")

subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit", "-m", "fix: expose backfill errors + verbose checkpoints"],
    check=False
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — restart the service and check logs")
