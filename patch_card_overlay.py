#!/usr/bin/env python3
"""Patch: lighter overlay bar, replace likes icon with heart, brighter icons."""

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
        changes.append(f"  ✗ SKIPPED: {label}")

# ── Reduce bar opacity: 0.82 → 0.65 ──────────────────────────────────────────
rep(
    "background: rgba(0,0,0,0.82);",
    "background: rgba(0,0,0,0.65);",
    "overlay opacity 0.82→0.65"
)

# ── Brighter icons: 0.70 → 0.90 ──────────────────────────────────────────────
rep(
    "fill: rgba(255,255,255,0.70);",
    "fill: rgba(255,255,255,0.90);",
    "icon fill 0.70→0.90"
)

# ── Replace thumbs-up with heart icon ─────────────────────────────────────────
OLD_LIKE = """const LIKE = `<svg viewBox="0 0 24 24"><path d="M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/></svg>`;"""
NEW_LIKE = """const LIKE = `<svg viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>`;"""

rep(OLD_LIKE, NEW_LIKE, "likes icon: thumbs-up → heart")

open(HTML_PATH, "w").write(html)
for c in changes:
    print(c)

subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit",
     "-m", "style: lighter overlay (0.65), heart icon, brighter icons"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — reload dashboard (no restart needed).")
