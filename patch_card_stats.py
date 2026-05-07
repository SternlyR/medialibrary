#!/usr/bin/env python3
"""Patch: larger card stats, solid dark bar background for readability."""

import os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")
HTML_PATH = os.path.join(ROOT, "frontend/youtube-shorts/index.html")
html = open(HTML_PATH).read()

changes = []

def rep(old, new, label):
    global html
    if old in html:
        html = html.replace(old, new)
        changes.append(f"  ✓ {label}")
    else:
        changes.append(f"  ✗ SKIPPED (no match): {label}")

# ── Solid dark bar instead of gradient ────────────────────────────────────────
rep(
    "  .card-overlay {\n    position: absolute;\n    bottom: 0; left: 0; right: 0;\n    padding: 28px 14px 12px;\n    background: linear-gradient(to top, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.4) 60%, transparent 100%);\n    z-index: 2;\n  }",
    "  .card-overlay {\n    position: absolute;\n    bottom: 0; left: 0; right: 0;\n    padding: 16px 18px;\n    background: rgba(0,0,0,0.82);\n    z-index: 2;\n  }",
    "overlay: gradient → solid dark bar"
)

# ── Stats container gap ────────────────────────────────────────────────────────
rep(
    "  .card-stats {\n    display: flex;\n    align-items: center;\n    gap: 14px;\n  }",
    "  .card-stats {\n    display: flex;\n    align-items: center;\n    gap: 22px;\n  }",
    "card-stats gap 14→22px"
)

# ── Stat text: 13px → 22px ────────────────────────────────────────────────────
rep(
    "  .cs {\n    display: flex;\n    align-items: center;\n    gap: 5px;\n    font-size: 13px;\n    font-weight: 700;\n    color: #fff;\n    letter-spacing: -0.3px;\n  }",
    "  .cs {\n    display: flex;\n    align-items: center;\n    gap: 8px;\n    font-size: 22px;\n    font-weight: 700;\n    color: #fff;\n    letter-spacing: -0.5px;\n  }",
    "cs font 13→22px"
)

# ── Stat icons: 12px → 20px, brighter ─────────────────────────────────────────
rep(
    "  .cs svg { width: 12px; height: 12px; fill: rgba(255,255,255,0.55); flex-shrink: 0; }",
    "  .cs svg { width: 20px; height: 20px; fill: rgba(255,255,255,0.70); flex-shrink: 0; }",
    "cs svg 12→20px, opacity 0.55→0.70"
)

open(HTML_PATH, "w").write(html)
for c in changes:
    print(c)

subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit",
     "-m", "style: larger card stats (22px), solid dark overlay bar"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — reload dashboard (no restart needed).")
