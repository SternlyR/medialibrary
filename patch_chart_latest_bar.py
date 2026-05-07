#!/usr/bin/env python3
"""Patch: fix chart label, highlight only the most recent bar (not global max)."""

import os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")
HTML_PATH = os.path.join(ROOT, "frontend/youtube-shorts/index.html")
html = open(HTML_PATH).read()

# Fix label text if it still says "Last 24 Hours"
html = html.replace("Views · Last 24 Hours", "Views · Last 7 Days")
html = html.replace("VIEWS · LAST 24 HOURS", "Views · Last 7 Days")

# Change "Peak" label to "Latest" — we'll show the most recent bar value
html = html.replace(
    '<span id="chart-peak">Peak <strong id="peak-val">—</strong></span>',
    '<span id="chart-peak">Latest <strong id="peak-val">—</strong></span>',
)

# Change peak highlight logic: brighten last bar, not global max
html = html.replace(
    """  const peak=data.values.length?Math.max(...data.values):0;
  document.getElementById("peak-val").textContent=fmt(peak)+" views";""",
    """  const lastIdx=data.values.length-1;
  const lastVal=lastIdx>=0?data.values[lastIdx]:0;
  document.getElementById("peak-val").textContent=fmt(lastVal)+" views";""",
)

# Update bar color callback to use lastIdx instead of peak value comparison
html = html.replace(
    'backgroundColor:(ctx)=>ctx.raw===peak?"rgba(65,180,217,1)":"rgba(65,180,217,0.6)",',
    'backgroundColor:(ctx)=>ctx.dataIndex===lastIdx?"rgba(65,180,217,1)":"rgba(65,180,217,0.55)",',
)

# Also handle if the patch_ui_blue was never applied (still red)
html = html.replace(
    'backgroundColor:(ctx)=>ctx.raw===peak?"rgba(255,0,0,0.95)":"rgba(255,0,0,0.45)",',
    'backgroundColor:(ctx)=>ctx.dataIndex===lastIdx?"rgba(65,180,217,1)":"rgba(65,180,217,0.55)",',
)
html = html.replace(
    'hoverBackgroundColor:"rgba(255,0,0,0.9)",',
    'hoverBackgroundColor:"rgba(65,180,217,1)",',
)
html = html.replace(
    'bodyColor:"#fff",',
    'bodyColor:"rgba(65,180,217,1)",',
)

open(HTML_PATH, "w").write(html)
print("  updated chart: latest-bar highlight + label fix")

subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit", "-m", "fix: highlight most recent chart bar, fix 7-day label"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — reload the dashboard to see the change (no restart needed).")
