#!/usr/bin/env python3
"""Patch: dynamic subscriber count, YouTube logo image, @FullSquad handle only."""

import os, subprocess, re

ROOT = os.path.expanduser("~/insight-dashboards")

# ── 1. Add get_subscriber_count to api.py ─────────────────────────────────────
API_PATH = os.path.join(ROOT, "dashboards/youtube/api.py")
api = open(API_PATH).read()

if "get_subscriber_count" not in api:
    api = api.replace(
        "    async def get_channel_view_count(",
        """    async def get_subscriber_count(self, channel_id: str) -> int:
        \"\"\"Returns current subscriber count for the channel.\"\"\"
        params = {"part": "statistics", "id": channel_id, "key": self.api_key}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{self.BASE}/channels", params=params)
            r.raise_for_status()
            data = r.json()
        items = data.get("items", [])
        if not items:
            return 0
        return int(items[0].get("statistics", {}).get("subscriberCount", 0))

    async def get_channel_view_count(""",
    )
    open(API_PATH, "w").write(api)
    print("  added get_subscriber_count to api.py")
else:
    print("  get_subscriber_count already in api.py — skipping")

# ── 2. Add /api/youtube/channel endpoint to server.py ─────────────────────────
SERVER_PATH = os.path.join(ROOT, "dashboards/server.py")
server = open(SERVER_PATH).read()

if "/api/youtube/channel" not in server:
    server = server.replace(
        "@app.get(\"/api/youtube/metrics\")",
        """@app.get("/api/youtube/channel")
async def api_channel():
    from dashboards.youtube.api import YouTubeAPI
    from dashboards.config import settings
    api = YouTubeAPI(settings.youtube_api_key)
    subs = await api.get_subscriber_count(settings.youtube_channel_id)
    return {"subscribers": subs}


@app.get("/api/youtube/metrics")""",
    )
    open(SERVER_PATH, "w").write(server)
    print("  added /api/youtube/channel endpoint to server.py")
else:
    print("  /api/youtube/channel already in server.py — skipping")

# ── 3. Patch index.html ────────────────────────────────────────────────────────
HTML_PATH = os.path.join(ROOT, "frontend/youtube-shorts/index.html")
html = open(HTML_PATH).read()

# 3a. Replace channel-icon div with YouTube logo img
OLD_ICON_HTML = """      <div id="channel-icon">
        <svg viewBox="0 0 24 24"><path d="M10 15.5l6-3.5-6-3.5v7z"/></svg>
      </div>"""

NEW_ICON_HTML = """      <img id="channel-icon" src="/assets/yt-logo.png" alt="YouTube">"""

if OLD_ICON_HTML in html:
    html = html.replace(OLD_ICON_HTML, NEW_ICON_HTML)
    print("  replaced channel-icon SVG with YouTube logo img")
else:
    print("  WARNING: channel-icon HTML not matched — skipping icon swap")

# 3b. Update channel-icon CSS for img display
OLD_ICON_CSS = """  #channel-icon {
    width: 52px; height: 37px;
    background: #FF0000;
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
  }
  #channel-icon svg { width: 20px; height: 20px; fill: #fff; }"""

NEW_ICON_CSS = """  #channel-icon {
    height: 28px;
    width: auto;
    object-fit: contain;
    flex-shrink: 0;
  }"""

if OLD_ICON_CSS in html:
    html = html.replace(OLD_ICON_CSS, NEW_ICON_CSS)
else:
    # fallback: replace any #channel-icon block
    html = re.sub(
        r'#channel-icon \{[^}]+\}\s*#channel-icon svg \{[^}]+\}',
        NEW_ICON_CSS, html
    )
print("  updated #channel-icon CSS for img")

# 3c. Update handle to "@FullSquad" only + subscriber count span
OLD_HANDLE = '        <div id="channel-handle">@FullSquad · Shorts</div>'
NEW_HANDLE = '        <div id="channel-handle">@FullSquad · <span id="sub-count">—</span> subscribers</div>'

if OLD_HANDLE in html:
    html = html.replace(OLD_HANDLE, NEW_HANDLE)
    print("  updated handle to @FullSquad with subscriber count span")
else:
    html = html.replace(
        '>@FullSquad · Shorts<',
        '>@FullSquad · <span id="sub-count">—</span> subscribers<'
    )
    print("  updated handle (fallback match)")

# 3d. Add fmtSub function and subscriber fetch to the JS
# fmtSub always shows 2 decimals for <10M, 1 decimal for 10-99M, none for 100M+
OLD_FMT_BLOCK = "function fmt(n){"
NEW_FMT_BLOCK = """function fmtSub(n){
  if(n>=1e9){const v=n/1e9;return v.toFixed(v>=10?1:2)+"B";}
  if(n>=1e6){const v=n/1e6;return v.toFixed(v>=100?0:v>=10?1:2)+"M";}
  if(n>=1e3){const v=n/1e3;return v.toFixed(v>=100?0:v>=10?1:2)+"K";}
  return Math.round(n).toLocaleString();
}
function fmt(n){"""

if "fmtSub" not in html:
    html = html.replace(OLD_FMT_BLOCK, NEW_FMT_BLOCK)
    print("  added fmtSub() formatter")

# 3e. Add subscriber fetch to the refresh() function
OLD_FETCH = """    const [vRes,mRes,cRes] = await Promise.all([
      fetch("/api/youtube/videos"),
      fetch("/api/youtube/metrics"),"""

NEW_FETCH = """    const [vRes,mRes,cRes,chRes] = await Promise.all([
      fetch("/api/youtube/videos"),
      fetch("/api/youtube/metrics"),"""

OLD_DESTRUCTURE = '    const [vids,metrics,chartData] = await Promise.all([vRes.json(),mRes.json(),cRes.json()]);'
NEW_DESTRUCTURE = '    const [vids,metrics,chartData,chData] = await Promise.all([vRes.json(),mRes.json(),cRes.json(),chRes.json()]);\n    const subEl=document.getElementById("sub-count"); if(subEl&&chData.subscribers) subEl.textContent=fmtSub(chData.subscribers);'

if OLD_FETCH in html:
    html = html.replace(OLD_FETCH, NEW_FETCH)
    # Add the chart fetch to Promise.all
    html = html.replace(
        "      fetch(\"/api/youtube/chart\"),",
        "      fetch(\"/api/youtube/chart\"),\n      fetch(\"/api/youtube/channel\"),"
    )
    print("  added subscriber fetch to Promise.all")

if OLD_DESTRUCTURE in html:
    html = html.replace(OLD_DESTRUCTURE, NEW_DESTRUCTURE)
    print("  added subscriber count display to refresh()")

open(HTML_PATH, "w").write(html)

# ── 4. Commit ──────────────────────────────────────────────────────────────────
subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
r = subprocess.run(
    ["git", "-C", ROOT, "commit",
     "-m", "feat: YouTube logo, dynamic subscriber count, @FullSquad handle"],
    check=False,
)
if r.returncode not in (0, 1):
    raise SystemExit(f"git commit failed: {r.returncode}")
print("\nDone — restart the service (new API endpoint added), then reload dashboard.")
