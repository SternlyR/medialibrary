#!/usr/bin/env python3
"""Patch: fix OAuth field names in config.py and cache.py to match .env variables.

The .env uses YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN but the previous patch
added google_* field names. This corrects them to match.
"""

import os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")

# ── 1. Fix config.py field names ──────────────────────────────────────────────
CONFIG_PATH = os.path.join(ROOT, "dashboards/config.py")
config = open(CONFIG_PATH).read()

# Remove google_ fields if present, add youtube_ fields
for old in ["google_client_id: str = \"\"", "google_client_secret: str = \"\"", "google_refresh_token: str = \"\""]:
    config = config.replace("\n    " + old, "")

if "youtube_client_id" not in config:
    config = config.replace(
        "    youtube_refresh_interval_minutes: int = 15",
        """    youtube_refresh_interval_minutes: int = 15
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_refresh_token: str = \"\""""
    )
    open(CONFIG_PATH, "w").write(config)
    print("  updated config.py: using youtube_client_id/secret/refresh_token")
else:
    print("  config.py already has youtube_client_id — skipping")

# ── 2. Fix cache.py get_metrics_30d() to use correct field names ───────────────
CACHE_PATH = os.path.join(ROOT, "dashboards/youtube/cache.py")
cache = open(CACHE_PATH).read()

# Fix field name references (google_ → youtube_)
cache = cache.replace("settings.google_client_id", "settings.youtube_client_id")
cache = cache.replace("settings.google_client_secret", "settings.youtube_client_secret")
cache = cache.replace("settings.google_refresh_token", "settings.youtube_refresh_token")

open(CACHE_PATH, "w").write(cache)
print("  updated cache.py: settings.google_* → settings.youtube_*")

# ── 3. Commit ──────────────────────────────────────────────────────────────────
subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit", "-m", "fix: OAuth field names match YOUTUBE_ env vars"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — apply patch_analytics_metrics first if not done, then this patch, then restart.")
