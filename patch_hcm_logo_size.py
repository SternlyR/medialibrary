#!/usr/bin/env python3
"""Patch: increase HCM brand logo height to match YouTube logo."""

import os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")
HTML_PATH = os.path.join(ROOT, "frontend/youtube-shorts/index.html")
html = open(HTML_PATH).read()

html = html.replace(
    "  #brand-logo {\n    height: 48px;\n    width: auto;\n    object-fit: contain;\n    opacity: 0.92;\n  }",
    "  #brand-logo {\n    height: 64px;\n    width: auto;\n    object-fit: contain;\n    opacity: 0.92;\n  }",
)

open(HTML_PATH, "w").write(html)
print("  updated #brand-logo height: 48px → 64px")

subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(["git", "-C", ROOT, "commit", "-m", "style: increase HCM logo height to 64px"], check=False)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — reload dashboard (no restart needed).")
