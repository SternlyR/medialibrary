#!/usr/bin/env python3
"""Patch: fill chart gaps with interpolated hourly estimates.

When there's a gap between two snapshots (service restart, API failure, etc.),
distribute the total delta evenly across the missing hours instead of leaving
the chart blank. Live data fills in naturally as it accumulates.

Also switches to hourly bucketing (max total_views per hour) so each bar
represents exactly one hour, giving a clean 168-bar 7-day view.
"""

import os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")
CACHE_PATH = os.path.join(ROOT, "dashboards/youtube/cache.py")

OLD_CHART = '''async def get_chart_data(hours: int = 24) -> dict:
    """Returns labels + values for a bar chart of view deltas per snapshot interval."""
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

    labels = []
    values = []
    for i in range(1, len(snaps)):
        delta = max(0, snaps[i].total_views - snaps[i - 1].total_views)
        ts = snaps[i].timestamp
        labels.append(f"{ts.hour:02d}:{ts.minute:02d}")
        values.append(delta)

    return {"labels": labels, "values": values}'''

NEW_CHART = '''async def get_chart_data(hours: int = 168) -> dict:
    """Returns hourly view deltas for a bar chart, with gaps filled by interpolation."""
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

    # Group into hourly buckets: highest total_views seen within each hour
    hourly: dict = {}
    for snap in snaps:
        bucket = snap.timestamp.replace(minute=0, second=0, microsecond=0)
        if bucket not in hourly or snap.total_views > hourly[bucket]:
            hourly[bucket] = snap.total_views

    sorted_buckets = sorted(hourly.keys())
    if len(sorted_buckets) < 2:
        return {"labels": [], "values": []}

    labels = []
    values = []
    for i in range(1, len(sorted_buckets)):
        prev_b = sorted_buckets[i - 1]
        curr_b = sorted_buckets[i]
        gap_hours = max(1, round((curr_b - prev_b).total_seconds() / 3600))
        total_delta = max(0, hourly[curr_b] - hourly[prev_b])

        if gap_hours == 1:
            # Normal consecutive hour
            labels.append(curr_b.strftime("%m/%d %H:00"))
            values.append(total_delta)
        else:
            # Gap: distribute the known delta evenly across missing hours
            per_hour = round(total_delta / gap_hours)
            for h in range(gap_hours):
                hour_ts = prev_b + timedelta(hours=h + 1)
                labels.append(hour_ts.strftime("%m/%d %H:00"))
                values.append(per_hour)

    return {"labels": labels, "values": values}'''

cache = open(CACHE_PATH).read()

if "gap_hours" in cache:
    print("  gap-filling already in get_chart_data() — skipping")
elif OLD_CHART in cache:
    cache = cache.replace(OLD_CHART, NEW_CHART)
    open(CACHE_PATH, "w").write(cache)
    print("  updated get_chart_data(): hourly bucketing + gap interpolation")
else:
    # Fallback: try replacing just the function signature + body with a looser match
    import re
    if re.search(r'async def get_chart_data', cache):
        cache = re.sub(
            r'async def get_chart_data\(hours.*?return \{"labels": labels, "values": values\}',
            NEW_CHART,
            cache,
            flags=re.DOTALL,
        )
        open(CACHE_PATH, "w").write(cache)
        print("  updated get_chart_data() via regex fallback")
    else:
        print("  ERROR: could not find get_chart_data() in cache.py")
        raise SystemExit(1)

subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit",
     "-m", "feat: fill chart gaps with interpolated hourly estimates"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — no restart needed, reload the dashboard to see the filled chart.")
