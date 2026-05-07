#!/usr/bin/env python3
"""Patch: add static file serving, swap YouTube badge for brand logo, bigger stat numbers."""

import os, subprocess

ROOT = os.path.expanduser("~/insight-dashboards")

# ── 1. Create assets directory ────────────────────────────────────────────────
ASSETS_DIR = os.path.join(ROOT, "frontend/assets")
os.makedirs(ASSETS_DIR, exist_ok=True)
print(f"  created {ASSETS_DIR}")

# ── 2. Add StaticFiles mount to server.py ─────────────────────────────────────
SERVER_PATH = os.path.join(ROOT, "dashboards/server.py")
server = open(SERVER_PATH).read()

if "StaticFiles" not in server:
    server = server.replace(
        "from fastapi import FastAPI\nfrom fastapi.responses import HTMLResponse, RedirectResponse",
        "from fastapi import FastAPI\nfrom fastapi.responses import HTMLResponse, RedirectResponse\nfrom fastapi.staticfiles import StaticFiles",
    )
    # Mount after app is created
    server = server.replace(
        'app = FastAPI(title="Insight Dashboards", lifespan=lifespan)',
        'app = FastAPI(title="Insight Dashboards", lifespan=lifespan)\napp.mount("/assets", StaticFiles(directory=str(FRONTEND / "assets")), name="assets")',
    )
    open(SERVER_PATH, "w").write(server)
    print("  added StaticFiles mount to server.py (/assets → frontend/assets/)")
else:
    print("  StaticFiles already in server.py — skipping")

# ── 3. Patch index.html ────────────────────────────────────────────────────────
HTML_PATH = os.path.join(ROOT, "frontend/youtube-shorts/index.html")
html = open(HTML_PATH).read()

# 3a. Bigger stat numbers: 64px → 84px, tighten letter-spacing slightly
html = html.replace(
    "  .stat-value {\n    font-size: 64px;\n    font-weight: 800;\n    letter-spacing: -3px;",
    "  .stat-value {\n    font-size: 84px;\n    font-weight: 800;\n    letter-spacing: -4px;",
)

# 3b. Replace YouTube badge with brand logo img
OLD_BADGE = """    <div id="yt-badge">
      <svg viewBox="0 0 90 20" fill="none">
        <path d="M27.9 3.5a3.5 3.5 0 0 0-2.5-2.5C23.2.5 14.5.5 14.5.5S5.8.5 3.6 1c-1.2.3-2.2 1.3-2.5 2.5C.6 5.7.6 10 .6 10s0 4.3.5 6.5c.3 1.2 1.3 2.2 2.5 2.5 2.2.6 10.9.6 10.9.6s8.7 0 10.9-.6c1.2-.3 2.2-1.3 2.5-2.5.5-2.2.5-6.5.5-6.5s0-4.3-.5-6.5z" fill="#FF0000"/>
        <path d="M11.8 14l7.2-4-7.2-4v8z" fill="#fff"/>
      </svg>
      <span>Live</span>
    </div>"""

NEW_BADGE = """    <img id="brand-logo" src="/assets/logo.png" alt="Hard Carry Media">"""

# Also remove the now-unused #yt-badge CSS and add brand-logo CSS
OLD_BADGE_CSS = """  #yt-badge {
    display: flex; align-items: center; gap: 7px;
    padding: 6px 14px;
    border: 1px solid var(--border);
    border-radius: 6px;
  }
  #yt-badge svg { width: 22px; height: 16px; }
  #yt-badge span { font-size: 11px; color: var(--dim); letter-spacing: 0.5px; text-transform: uppercase; }"""

NEW_LOGO_CSS = """  #brand-logo {
    height: 48px;
    width: auto;
    object-fit: contain;
    opacity: 0.92;
  }"""

if OLD_BADGE in html:
    html = html.replace(OLD_BADGE, NEW_BADGE)
    print("  replaced YouTube badge with brand logo img tag")
else:
    print("  WARNING: YouTube badge HTML not found — logo img tag not inserted")

if OLD_BADGE_CSS in html:
    html = html.replace(OLD_BADGE_CSS, NEW_LOGO_CSS)
    print("  replaced #yt-badge CSS with #brand-logo CSS")
else:
    # Just append the new CSS before </style>
    html = html.replace("</style>", f"{NEW_LOGO_CSS}\n</style>", 1)
    print("  appended #brand-logo CSS")

open(HTML_PATH, "w").write(html)

# ── 4. Commit ──────────────────────────────────────────────────────────────────
subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit",
     "-m", "feat: brand logo in header, larger stat numbers, static file serving"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")

print("\nDone.")
print("Next steps:")
print("  1. Upload your logo:  scp logo.png sean@mediaserver:~/insight-dashboards/frontend/assets/logo.png")
print("  2. Restart service:   pkill -f 'python run.py'; pkill -f uvicorn; sleep 2 && .venv/bin/python run.py >> ~/dashboard.log 2>&1 &")
print("  3. Reload dashboard in browser")
print("")
print("Logo will display at 48px height. If it needs sizing adjustments let me know.")
