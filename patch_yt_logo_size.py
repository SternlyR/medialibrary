#!/usr/bin/env python3
"""Patch: increase YouTube logo height to match channel name + handle block."""

import os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")
HTML_PATH = os.path.join(ROOT, "frontend/youtube-shorts/index.html")
html = open(HTML_PATH).read()

html = html.replace(
    "  #channel-icon {\n    height: 28px;\n    width: auto;\n    object-fit: contain;\n    flex-shrink: 0;\n  }",
    "  #channel-icon {\n    height: 44px;\n    width: auto;\n    object-fit: contain;\n    flex-shrink: 0;\n  }",
)

open(HTML_PATH, "w").write(html)
print("  updated #channel-icon height: 28px → 44px")

subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(["git", "-C", ROOT, "commit", "-m", "style: increase YouTube logo height to 44px"], check=False)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("Done — reload dashboard (no restart needed).")
