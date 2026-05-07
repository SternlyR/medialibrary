#!/usr/bin/env python3
"""Patch: replace hourly weight curve with softer 2:1 ratio (was 4.3:1).
Also clears old synthetic snapshots so backfill reruns with corrected weights.
"""

import os, re, sqlite3, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")

# ── 1. Update _HOURLY_WEIGHTS in cache.py via regex (immune to comment differences)
CACHE_PATH = os.path.join(ROOT, "dashboards/youtube/cache.py")
cache = open(CACHE_PATH).read()

NEW_WEIGHTS = """_HOURLY_WEIGHTS = [
    4.5, 4.3, 4.0, 3.6, 3.2, 2.9,   # UTC 00-05
    2.8, 2.7, 2.8, 2.9, 3.2, 3.6,   # UTC 06-11
    3.9, 4.1, 4.3, 4.5, 4.8, 5.1,   # UTC 12-17
    5.3, 5.2, 5.0, 4.8, 4.5, 4.3,   # UTC 18-23  (peak ~1PM-6PM CDT)
]"""

updated = re.sub(
    r'_HOURLY_WEIGHTS\s*=\s*\[[\s\S]*?\]',
    NEW_WEIGHTS,
    cache,
)

if updated == cache:
    print("  ERROR: _HOURLY_WEIGHTS not found in cache.py — check the file manually")
else:
    open(CACHE_PATH, "w").write(updated)
    print("  updated _HOURLY_WEIGHTS (ratio now ~2:1)")

# ── 2. Delete synthetic snapshots older than 24h so backfill reruns
DB_PATH = os.path.join(ROOT, "insight.db")
if os.path.exists(DB_PATH):
    con = sqlite3.connect(DB_PATH)
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(hours=24)
    deleted = con.execute(
        "DELETE FROM youtube_views_snapshots WHERE timestamp < ?",
        (cutoff.strftime("%Y-%m-%d %H:%M:%S"),)
    ).rowcount
    con.commit()
    con.close()
    print(f"  cleared {deleted} synthetic snapshots — backfill will rerun on next refresh")

# ── 3. Commit cache.py change
subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit", "-m", "fix: soften hourly backfill curve to ~2:1 ratio"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — restart the service to trigger backfill with corrected weights.")
