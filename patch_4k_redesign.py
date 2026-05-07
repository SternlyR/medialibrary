#!/usr/bin/env python3
"""Patch: full 4K redesign — 2160×3840px canvas, all values native for 4K portrait TV."""

import os, re, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")
HTML_PATH = os.path.join(ROOT, "frontend/youtube-shorts/index.html")
html = open(HTML_PATH).read()

# ── 1. Viewport meta tag ──────────────────────────────────────────────────────
html = re.sub(
    r'<meta name="viewport" content="width=\d+">',
    '<meta name="viewport" content="width=2160">',
    html,
)
print("  viewport → width=2160")

# ── 2. Replace entire <style> block ───────────────────────────────────────────
NEW_STYLE = """<style>
  :root {
    --red:      #FF0000;
    --bg:       #080808;
    --surface:  #0f0f0f;
    --border:   #1e1e1e;
    --text:     #ffffff;
    --dim:      #606060;
    --dim2:     #3a3a3a;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  html, body {
    width: 2160px;
    height: 3840px;
    overflow: hidden;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
  }

  body { display: flex; flex-direction: column; }

  /* ── Header ─────────────────────────────────────────── */
  #header {
    flex: 0 0 540px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 0 96px;
    background: var(--surface);
    border-bottom: 2px solid var(--border);
    gap: 56px;
  }

  #header-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  #channel-id {
    display: flex;
    align-items: center;
    gap: 40px;
  }

  #channel-icon {
    height: 112px;
    width: auto;
    object-fit: contain;
    flex-shrink: 0;
  }

  #channel-name {
    font-size: 52px;
    font-weight: 700;
    letter-spacing: -0.6px;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  #channel-name svg { width: 36px; height: 36px; flex-shrink: 0; }
  #channel-handle { font-size: 32px; color: var(--dim); margin-top: 8px; }

  #brand-logo {
    height: 128px;
    width: auto;
    object-fit: contain;
    opacity: 0.92;
  }

  /* ── Stat row ────────────────────────────────────────── */
  #stat-row {
    display: flex;
    align-items: stretch;
    justify-content: center;
  }

  .stat {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    padding: 0 96px;
    border-right: 2px solid var(--border);
  }
  .stat:last-child { border-right: none; }

  .stat-value {
    font-size: 168px;
    font-weight: 800;
    letter-spacing: -8px;
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }
  .stat-value.accent { color: #0074fc; }

  .stat-meta {
    font-size: 26px;
    color: var(--dim);
    text-transform: uppercase;
    letter-spacing: 3px;
  }

  /* ── Section label ───────────────────────────────────── */
  #section-label {
    flex: 0 0 76px;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    padding: 0 72px;
    border-bottom: 2px solid var(--border);
  }

  #section-title {
    font-size: 20px;
    font-weight: 700;
    color: var(--dim);
    text-transform: uppercase;
    letter-spacing: 4px;
  }

  #refresh-info {
    position: absolute;
    right: 72px;
    font-size: 20px;
    color: var(--dim2);
  }
  #refresh-info span { color: var(--dim); }

  /* ── Grid ────────────────────────────────────────────── */
  #grid {
    flex: 1;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    grid-template-rows: repeat(3, 1fr);
    gap: 4px;
    background: #000;
  }

  .card {
    position: relative;
    overflow: hidden;
    background: #000;
  }

  .card-thumb {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .card-player {
    position: absolute;
    inset: -10% -10%;
    width: 120%;
    height: 120%;
    pointer-events: none;
    background: #000;
    z-index: 1;
  }

  .card.playing .card-player { opacity: 1; }

  .card-overlay {
    position: absolute;
    bottom: 0; left: 0; right: 0;
    padding: 32px 36px;
    background: rgba(0,0,0,0.65);
    z-index: 2;
  }

  .card-stats {
    display: flex;
    align-items: center;
    gap: 44px;
  }

  .cs {
    display: flex;
    align-items: center;
    gap: 16px;
    font-size: 44px;
    font-weight: 700;
    color: #fff;
    letter-spacing: -1px;
  }
  .cs svg { width: 40px; height: 40px; fill: rgba(255,255,255,0.90); flex-shrink: 0; }

  /* ── Chart section ───────────────────────────────────── */
  #chart-section {
    flex: 0 0 372px;
    background: var(--surface);
    border-top: 2px solid var(--border);
    padding: 24px 56px 20px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  #chart-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  #chart-label {
    font-size: 20px;
    font-weight: 700;
    color: var(--dim);
    text-transform: uppercase;
    letter-spacing: 4px;
  }

  #chart-peak { font-size: 20px; color: var(--dim2); }
  #chart-peak strong { color: var(--dim); font-weight: 600; }

  #chart-wrap { flex: 1; position: relative; }
  canvas { display: block; }
</style>"""

html = re.sub(r'<style>[\s\S]*?</style>', NEW_STYLE, html, count=1)
print("  replaced <style> block with native 4K CSS (2160×3840)")

# ── 3. Update Chart.js y-axis tick font size ──────────────────────────────────
html = re.sub(
    r'font:\{size:\d+\}',
    'font:{size:16}',
    html,
)
print("  chart y-axis tick font → 16px")

open(HTML_PATH, "w").write(html)

subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit",
     "-m", "feat: native 4K portrait canvas (2160×3840), all elements sized for 4K TV"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — reload dashboard. Set Chrome to 100% zoom on the 4K TV for native rendering.")
