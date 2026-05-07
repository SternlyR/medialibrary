#!/usr/bin/env python3
"""Patch: fix chart endpoint to request 7-day window (hours=168) instead of 24h."""

import os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")
path = os.path.join(ROOT, "dashboards/server.py")

content = open(path).read()
content = content.replace("get_chart_data(hours=24)", "get_chart_data(hours=168)")
open(path, "w").write(content)
print("  patched server.py: hours=24 → hours=168")

subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit", "-m", "fix: chart endpoint 24h -> 168h (7-day view)"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — restart the service.")
