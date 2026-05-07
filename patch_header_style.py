#!/usr/bin/env python3
"""Patch: number formatting, views color, YouTube logo + channel name with checkmark."""

import os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")
HTML_PATH = os.path.join(ROOT, "frontend/youtube-shorts/index.html")
html = open(HTML_PATH).read()

# ── 1. Fix fmt() — 3-digit integer = no decimal, 1-2 digit = 1 decimal ─────────
OLD_FMT = "function fmt(n){\n  if(n>=1e6) return(n/1e6).toFixed(n>=10e6?1:2)+\"M\";\n  if(n>=1e3) return(n/1e3).toFixed(n>=100e3?0:1)+\"K\";\n  return n.toLocaleString();\n}"
NEW_FMT = """function fmt(n){
  if(n>=1e9){const v=n/1e9;return(v>=100?Math.round(v):v.toFixed(1))+"B";}
  if(n>=1e6){const v=n/1e6;return(v>=100?Math.round(v):v.toFixed(1))+"M";}
  if(n>=1e3){const v=n/1e3;return(v>=100?Math.round(v):v.toFixed(1))+"K";}
  return Math.round(n).toLocaleString();
}"""

if OLD_FMT in html:
    html = html.replace(OLD_FMT, NEW_FMT)
    print("  updated fmt(): 3 digits=no decimal, 1-2 digits=1 decimal")
else:
    print("  WARNING: fmt() not matched exactly — skipping (check manually)")

# ── 2. Views color: red → #0074fc ─────────────────────────────────────────────
html = html.replace(
    ".stat-value.accent { color: var(--red); }",
    ".stat-value.accent { color: #0074fc; }",
)
print("  changed views color to #0074fc")

# ── 3. YouTube logo icon (rounded rect + play triangle) ───────────────────────
OLD_ICON_CSS = """  #channel-icon {
    width: 44px; height: 44px;
    background: var(--red);
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
  }
  #channel-icon svg { width: 22px; height: 22px; fill: #fff; }"""

NEW_ICON_CSS = """  #channel-icon {
    width: 52px; height: 37px;
    background: #FF0000;
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
  }
  #channel-icon svg { width: 20px; height: 20px; fill: #fff; }"""

html = html.replace(OLD_ICON_CSS, NEW_ICON_CSS)
print("  updated channel-icon: circle → YouTube rounded-rect")

# The play button SVG path (same icon, shape change is via CSS)
# Already using a play triangle, which is correct for YouTube icon

# ── 4. Channel name + verified badge + handle ─────────────────────────────────
# Add verified badge CSS
VERIFIED_CSS = """  #channel-name { font-size: 15px; font-weight: 600; letter-spacing: -0.2px; }
  #channel-handle { font-size: 12px; color: var(--dim); margin-top: 1px; }"""

NEW_VERIFIED_CSS = """  #channel-name { font-size: 15px; font-weight: 600; letter-spacing: -0.2px; display: flex; align-items: center; gap: 5px; }
  #channel-name svg { width: 14px; height: 14px; flex-shrink: 0; }
  #channel-handle { font-size: 12px; color: var(--dim); margin-top: 1px; }"""

html = html.replace(VERIFIED_CSS, NEW_VERIFIED_CSS)

# Update the channel name element to include verified checkmark SVG
OLD_CHANNEL_HTML = """      <div>
        <div id="channel-name">Loading…</div>
        <div id="channel-handle">@channel · Shorts</div>
      </div>"""

NEW_CHANNEL_HTML = """      <div>
        <div id="channel-name">Full Squad Gaming <svg viewBox="0 0 24 24" fill="#aaa"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 14.5l-4-4 1.41-1.41L10 13.67l6.59-6.59L18 8.5l-8 8z"/></svg></div>
        <div id="channel-handle">@FullSquad · Shorts</div>
      </div>"""

if OLD_CHANNEL_HTML in html:
    html = html.replace(OLD_CHANNEL_HTML, NEW_CHANNEL_HTML)
    print("  updated channel name to Full Squad Gaming with verified badge")
else:
    print("  WARNING: channel name HTML not matched — skipping")

open(HTML_PATH, "w").write(html)

subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit",
     "-m", "style: fmt decimals, blue views, YouTube icon, channel name + verified"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — reload the dashboard (no restart needed, HTML-only change).")
