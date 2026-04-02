"""Background refresh service for the YouTube Shorts dashboard.

Responsibilities
────────────────
1. Every N minutes (configurable, default 15):
   - Fetch the latest Shorts from the configured channel via the YouTube API
   - Upsert them into the youtube_shorts table
   - Take a total-views snapshot for the 24-hr chart
2. Expose helper functions the API routes use to read cached data.

APScheduler runs inside the same uvicorn process via lifespan events.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, delete, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from medialibrary.config import settings
from medialibrary.database import init_db, get_session, YoutubeShort, YoutubeViewsSnapshot
from medialibrary.youtube_api import YouTubeAPI, YouTubeAPIError

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_engine = None


# ── Lifecycle ──────────────────────────────────────────────────────────────────

async def start_scheduler(engine=None) -> None:
    """Call this once from the FastAPI lifespan startup."""
    global _scheduler, _engine

    if not settings.youtube_api_key or not settings.youtube_channel_id:
        log.warning(
            "YouTube dashboard disabled — set YOUTUBE_API_KEY and "
            "YOUTUBE_CHANNEL_ID in .env to enable it."
        )
        return

    _engine = engine
    _scheduler = AsyncIOScheduler()
    interval = max(settings.youtube_refresh_interval_minutes, 5)

    _scheduler.add_job(
        _refresh_job,
        trigger="interval",
        minutes=interval,
        id="youtube_refresh",
        replace_existing=True,
        next_run_time=datetime.now(),   # run immediately on startup
    )
    _scheduler.start()
    log.info("YouTube refresh scheduler started (every %d min)", interval)


async def stop_scheduler() -> None:
    """Call this from the FastAPI lifespan shutdown."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("YouTube refresh scheduler stopped")


# ── Refresh job ────────────────────────────────────────────────────────────────

async def _refresh_job() -> None:
    global _engine
    try:
        if _engine is None:
            _engine = await init_db()

        channel_id = settings.youtube_channel_id
        api = YouTubeAPI(settings.youtube_api_key)
        try:
            shorts = await api.get_channel_shorts(channel_id, max_results=50)
        finally:
            await api.close()

        if not shorts:
            log.info("YouTube refresh: no shorts found for channel %s", channel_id)
            return

        now = datetime.now(timezone.utc)
        async with await get_session(_engine) as session:
            # Upsert each short
            for rank, s in enumerate(shorts, start=1):
                stmt = (
                    sqlite_insert(YoutubeShort)
                    .values(
                        channel_id=channel_id,
                        video_id=s.video_id,
                        title=s.title,
                        description=s.description,
                        published_at=s.published_at.replace(tzinfo=None)
                        if s.published_at else None,
                        thumbnail_url=s.thumbnail_url,
                        duration_seconds=s.duration_seconds,
                        views=s.views,
                        likes=s.likes,
                        comments=s.comments,
                        rank=rank,
                        updated_at=now.replace(tzinfo=None),
                    )
                    .on_conflict_do_update(
                        index_elements=["video_id"],
                        set_={
                            "title": s.title,
                            "thumbnail_url": s.thumbnail_url,
                            "duration_seconds": s.duration_seconds,
                            "views": s.views,
                            "likes": s.likes,
                            "comments": s.comments,
                            "rank": rank,
                            "updated_at": now.replace(tzinfo=None),
                        },
                    )
                )
                await session.execute(stmt)

            # Take a total-views snapshot
            total_views = sum(s.views for s in shorts)
            snapshot = YoutubeViewsSnapshot(
                channel_id=channel_id,
                timestamp=now.replace(tzinfo=None),
                total_views=total_views,
            )
            session.add(snapshot)

            # Prune snapshots older than 48 hours
            cutoff = (now - timedelta(hours=48)).replace(tzinfo=None)
            await session.execute(
                delete(YoutubeViewsSnapshot).where(
                    YoutubeViewsSnapshot.channel_id == channel_id,
                    YoutubeViewsSnapshot.timestamp < cutoff,
                )
            )

            await session.commit()

        log.info(
            "YouTube refresh complete: %d shorts cached, total views=%d",
            len(shorts),
            total_views,
        )

    except YouTubeAPIError as exc:
        log.error("YouTube API error during refresh: %s", exc)
    except Exception as exc:
        log.exception("Unexpected error during YouTube refresh: %s", exc)


# ── Read helpers (used by API routes) ─────────────────────────────────────────

async def get_cached_shorts(
    channel_id: str,
    limit: int = 9,
    engine=None,
) -> list[dict]:
    """Return top *limit* shorts sorted by rank (view count desc)."""
    eng = engine or _engine
    if eng is None:
        eng = await init_db()

    async with await get_session(eng) as session:
        result = await session.execute(
            select(YoutubeShort)
            .where(YoutubeShort.channel_id == channel_id)
            .order_by(YoutubeShort.rank)
            .limit(limit)
        )
        rows = result.scalars().all()

    return [_short_to_dict(r) for r in rows]


async def get_7day_metrics(channel_id: str, engine=None) -> dict:
    """Sum views/likes/comments from videos published in last 7 days (cached)."""
    eng = engine or _engine
    if eng is None:
        eng = await init_db()

    cutoff = datetime.utcnow() - timedelta(days=7)
    async with await get_session(eng) as session:
        result = await session.execute(
            select(
                func.coalesce(func.sum(YoutubeShort.views), 0),
                func.coalesce(func.sum(YoutubeShort.likes), 0),
                func.coalesce(func.sum(YoutubeShort.comments), 0),
            ).where(
                YoutubeShort.channel_id == channel_id,
                YoutubeShort.published_at >= cutoff,
            )
        )
        row = result.one()

    return {
        "views_7d": int(row[0]),
        "likes_7d": int(row[1]),
        "comments_7d": int(row[2]),
    }


async def get_chart_data(channel_id: str, hours: int = 24, engine=None) -> dict:
    """Return per-interval view *deltas* for a bar chart.

    Snapshots are taken every N minutes; this function computes the
    difference between consecutive snapshots so each bar shows "new views
    in that interval".  Missing intervals are filled with 0.
    """
    eng = engine or _engine
    if eng is None:
        eng = await init_db()

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    async with await get_session(eng) as session:
        result = await session.execute(
            select(YoutubeViewsSnapshot)
            .where(
                YoutubeViewsSnapshot.channel_id == channel_id,
                YoutubeViewsSnapshot.timestamp >= cutoff,
            )
            .order_by(YoutubeViewsSnapshot.timestamp)
        )
        snapshots = result.scalars().all()

    if not snapshots:
        return {"labels": [], "values": []}

    labels: list[str] = []
    values: list[int] = []

    for i, snap in enumerate(snapshots):
        label = snap.timestamp.strftime("%-I:%M %p") if hasattr(snap.timestamp, 'strftime') else str(snap.timestamp)
        delta = 0
        if i > 0:
            prev = snapshots[i - 1]
            delta = max(0, snap.total_views - prev.total_views)
        labels.append(label)
        values.append(delta)

    # Drop the first point (no delta to show) unless it's the only one
    if len(labels) > 1:
        labels = labels[1:]
        values = values[1:]

    return {"labels": labels, "values": values}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _short_to_dict(row: YoutubeShort) -> dict:
    return {
        "video_id": row.video_id,
        "title": row.title,
        "thumbnail_url": row.thumbnail_url,
        "duration_seconds": row.duration_seconds,
        "views": row.views,
        "likes": row.likes,
        "comments": row.comments,
        "rank": row.rank,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
