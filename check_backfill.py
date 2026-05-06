#!/usr/bin/env python3
"""Check backfill status: service logs + chart endpoint summary.
Run on NUC: python3 check_backfill.py
"""

import subprocess, json, urllib.request

print("=== Service logs (last 30 lines) ===")
r = subprocess.run(
    ["journalctl", "-u", "insight-dashboards", "-n", "30", "--no-pager"],
    capture_output=True, text=True
)
print(r.stdout or r.stderr or "(no journalctl output)")

print("\n=== Chart endpoint ===")
try:
    with urllib.request.urlopen("http://localhost:8001/api/youtube/chart", timeout=5) as resp:
        data = json.loads(resp.read())
    labels = data.get("labels", [])
    values = data.get("values", [])
    print(f"Data points: {len(labels)}")
    if labels:
        print(f"First: {labels[0]}  value={values[0]}")
        print(f"Last:  {labels[-1]}  value={values[-1]}")
        print(f"Max value: {max(values)}")
        print(f"Non-zero bars: {sum(1 for v in values if v > 0)}")
except Exception as e:
    print(f"Error: {e}")

print("\n=== Snapshot DB count ===")
try:
    import sqlite3, os
    db = os.path.expanduser("~/insight-dashboards/insight.db")
    con = sqlite3.connect(db)
    count = con.execute("SELECT COUNT(*) FROM youtube_views_snapshots").fetchone()[0]
    oldest = con.execute("SELECT MIN(timestamp) FROM youtube_views_snapshots").fetchone()[0]
    newest = con.execute("SELECT MAX(timestamp) FROM youtube_views_snapshots").fetchone()[0]
    con.close()
    print(f"Total snapshots: {count}")
    print(f"Oldest: {oldest}")
    print(f"Newest: {newest}")
except Exception as e:
    print(f"DB error: {e}")
