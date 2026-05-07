#!/usr/bin/env python3
"""Patch: larger header with bigger channel identity, shorter chart to compensate."""

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

# ── Header height: 192 → 270px ────────────────────────────────────────────────
rep("    flex: 0 0 192px;",
    "    flex: 0 0 270px;",
    "header height 192→270px")

# ── Header internal gap: 18 → 28px ───────────────────────────────────────────
rep("    gap: 18px;\n  }\n\n  #header-top",
    "    gap: 28px;\n  }\n\n  #header-top",
    "header gap 18→28px")

# ── Header padding: slightly more breathing room ──────────────────────────────
rep("    padding: 0 36px;\n    background: var(--surface);",
    "    padding: 0 48px;\n    background: var(--surface);",
    "header padding 36→48px")

# ── Channel icon (YouTube logo img) height: 44 → 56px ────────────────────────
rep("    height: 44px;\n    width: auto;\n    object-fit: contain;\n    flex-shrink: 0;",
    "    height: 56px;\n    width: auto;\n    object-fit: contain;\n    flex-shrink: 0;",
    "channel icon height 44→56px")

# ── Channel-id gap: 12 → 20px ─────────────────────────────────────────────────
rep("    gap: 12px;\n  }",
    "    gap: 20px;\n  }",
    "channel-id gap 12→20px")

# ── Channel name font: 15 → 26px ──────────────────────────────────────────────
rep("  #channel-name { font-size: 15px; font-weight: 600; letter-spacing: -0.2px;",
    "  #channel-name { font-size: 26px; font-weight: 700; letter-spacing: -0.3px;",
    "channel-name font 15→26px")

# ── Channel handle font: 12 → 16px ────────────────────────────────────────────
rep("  #channel-handle { font-size: 12px; color: var(--dim); margin-top: 1px; }",
    "  #channel-handle { font-size: 16px; color: var(--dim); margin-top: 4px; }",
    "channel-handle font 12→16px")

# ── Verified badge icon size: 14 → 18px ───────────────────────────────────────
rep("  #channel-name svg { width: 14px; height: 14px; flex-shrink: 0; }",
    "  #channel-name svg { width: 18px; height: 18px; flex-shrink: 0; }",
    "verified badge 14→18px")

# ── Stat meta label: 11 → 13px ────────────────────────────────────────────────
rep("    font-size: 11px;\n    color: var(--dim);\n    text-transform: uppercase;\n    letter-spacing: 1.5px;",
    "    font-size: 13px;\n    color: var(--dim);\n    text-transform: uppercase;\n    letter-spacing: 1.5px;",
    "stat-meta font 11→13px")

# ── Chart section: 264 → 186px (offset the header growth) ────────────────────
rep("    flex: 0 0 264px;",
    "    flex: 0 0 186px;",
    "chart height 264→186px")

# ── Chart padding: less vertical padding for shorter chart ────────────────────
rep("    padding: 18px 28px 14px;",
    "    padding: 12px 28px 10px;",
    "chart padding tightened")

open(HTML_PATH, "w").write(html)
for c in changes:
    print(c)

subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit",
     "-m", "style: larger header (270px), bigger channel identity, shorter chart (186px)"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — reload dashboard (no restart needed).")
