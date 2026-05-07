#!/usr/bin/env python3
"""Patch: targeted fix for total_views snapshot source in cache.py.

Replaces the single line that sums video views with a call to channels.list.
Uses a very specific line match that works regardless of surrounding comment text.
"""

import os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")
CACHE_PATH = os.path.join(ROOT, "dashboards/youtube/cache.py")

cache = open(CACHE_PATH).read()

TARGET = "        total_views = sum(s.views for s in shorts)"

if TARGET not in cache:
    if "get_channel_view_count" in cache:
        print("  get_channel_view_count already in cache.py — patch already applied")
    else:
        print("  ERROR: target line not found in cache.py — print it and check manually:")
        print("    grep -n 'total_views' ~/insight-dashboards/dashboards/youtube/cache.py")
    raise SystemExit(0)

REPLACEMENT = """        # Channel all-time cumulative viewCount — never drops when videos age out.
        try:
            total_views = await api.get_channel_view_count(channel_id)
            if total_views == 0:
                raise ValueError("returned 0")
        except Exception as _exc:
            print(f"[yt-cache] channel viewCount unavailable ({_exc}), using video sum")
            total_views = sum(s.views for s in shorts)"""

cache = cache.replace(TARGET, REPLACEMENT)
open(CACHE_PATH, "w").write(cache)
print("  patched cache.py: total_views now uses channels.list.statistics.viewCount")

# Verify the call to get_channel_view_count is present in api.py
API_PATH = os.path.join(ROOT, "dashboards/youtube/api.py")
if "get_channel_view_count" not in open(API_PATH).read():
    print("  ERROR: get_channel_view_count not found in api.py — run patch_channel_views_fix first")
    raise SystemExit(1)

subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit",
     "-m", "fix: use channels.list viewCount for snapshot total_views"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — restart the service. Chart bars will appear after 2+ refreshes (30 min).")
print("Verify with: grep -n 'get_channel_view_count' ~/insight-dashboards/dashboards/youtube/cache.py")
