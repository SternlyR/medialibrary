#!/usr/bin/env python3
"""Patch: YouTube blue chart bars, remove card rank badges, soften hourly curve."""

import os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")

# ── 1. Soften the hourly weight curve in cache.py ──────────────────────────────
# Original had 4.3:1 peak-to-trough ratio; new curve is ~2:1 to match actual YT data.
CACHE_PATH = os.path.join(ROOT, "dashboards/youtube/cache.py")
cache = open(CACHE_PATH).read()

OLD_WEIGHTS = """_HOURLY_WEIGHTS = [
    5.0, 4.5, 4.0, 3.2, 2.5, 2.0,   # UTC 00-05  (7PM-midnight CDT prior eve)
    1.7, 1.5, 1.7, 2.0, 2.5, 3.2,   # UTC 06-11  (1AM-6AM CDT)
    3.8, 4.2, 4.6, 5.0, 5.5, 6.0,   # UTC 12-17  (7AM-noon CDT, ramp)
    6.5, 6.2, 5.8, 5.5, 5.0, 4.6,   # UTC 18-23  (1PM-6PM CDT, peak)
]"""

NEW_WEIGHTS = """_HOURLY_WEIGHTS = [
    4.5, 4.3, 4.0, 3.6, 3.2, 2.9,   # UTC 00-05
    2.8, 2.7, 2.8, 2.9, 3.2, 3.6,   # UTC 06-11
    3.9, 4.1, 4.3, 4.5, 4.8, 5.1,   # UTC 12-17
    5.3, 5.2, 5.0, 4.8, 4.5, 4.3,   # UTC 18-23  (peak)
]"""

if OLD_WEIGHTS in cache:
    cache = cache.replace(OLD_WEIGHTS, NEW_WEIGHTS)
    open(CACHE_PATH, "w").write(cache)
    print("  softened hourly weight curve in cache.py")
else:
    print("  WARNING: weight array not found in cache.py — skipping curve update")

# ── 2. Update frontend HTML ────────────────────────────────────────────────────
HTML_PATH = os.path.join(ROOT, "frontend/youtube-shorts/index.html")
html = open(HTML_PATH).read()

# Remove .card-rank CSS block
html = html.replace("""
  .card-rank {
    position: absolute;
    top: 10px; left: 10px;
    z-index: 3;
    font-size: 11px;
    font-weight: 800;
    color: rgba(255,255,255,0.9);
    background: rgba(0,0,0,0.55);
    border: 1px solid rgba(255,255,255,0.1);
    width: 24px; height: 24px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    backdrop-filter: blur(4px);
  }""", "")

# Remove rank element creation and append in buildGrid
html = html.replace("""      const rank=document.createElement("div");
      rank.className="card-rank"; rank.textContent=i+1;

      card.append(thumb,playerDiv,overlay,rank);""",
"""      card.append(thumb,playerDiv,overlay);""")

# Change chart peak bar color: red → YouTube blue
html = html.replace(
    'backgroundColor:(ctx)=>ctx.raw===peak?"rgba(255,0,0,0.95)":"rgba(255,0,0,0.45)",',
    'backgroundColor:(ctx)=>ctx.raw===peak?"rgba(65,180,217,1)":"rgba(65,180,217,0.6)",',
)
html = html.replace(
    'hoverBackgroundColor:"rgba(255,0,0,0.9)",',
    'hoverBackgroundColor:"rgba(65,180,217,1)",',
)

# Update tooltip value color to blue
html = html.replace(
    'backgroundColor:"rgba(0,0,0,0.85)",\n          titleColor:"#888",bodyColor:"#fff",padding:8,',
    'backgroundColor:"rgba(0,0,0,0.85)",\n          titleColor:"#888",bodyColor:"rgba(65,180,217,1)",padding:8,',
)

open(HTML_PATH, "w").write(html)
print("  updated frontend: YouTube blue chart + removed rank badges")

# ── Commit ─────────────────────────────────────────────────────────────────────
subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit",
     "-m", "style: YouTube blue chart bars, remove rank badges, soften hourly curve"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — restart the service to pick up the curve change.")
