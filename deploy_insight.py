#!/usr/bin/env python3
"""
Bootstrap insight-dashboards on the NUC.

Run via:
  git -C ~/medialibrary fetch origin
  git -C ~/medialibrary show origin/claude/youtube-vertical-dashboards-7YBYa:deploy_insight.py | python3
"""
import base64, os, subprocess, sys
from pathlib import Path

ROOT = Path.home() / "insight-dashboards"
ROOT.mkdir(exist_ok=True)
(ROOT / "dashboards" / "youtube").mkdir(parents=True, exist_ok=True)
(ROOT / "frontend" / "youtube-shorts").mkdir(parents=True, exist_ok=True)

# ── File contents ─────────────────────────────────────────────────────────────

TEXT_FILES = {}

TEXT_FILES["requirements.txt"] = """\
fastapi==0.115.6
uvicorn[standard]==0.34.0
sqlalchemy==2.0.36
aiosqlite==0.20.0
httpx==0.28.1
apscheduler==3.10.4
pydantic==2.10.4
pydantic-settings==2.7.0
python-dotenv==1.0.1
"""

TEXT_FILES[".env.example"] = """\
# YouTube Data API v3 key -- free at https://console.cloud.google.com/
YOUTUBE_API_KEY=AIzaXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
# Your channel ID (e.g. UCxxxxxxxxxxxxxxxxxxxxxx)
YOUTUBE_CHANNEL_ID=UCxxxxxxxxxxxxxxxxxxxxxx
# How often to poll YouTube API (minutes). Minimum 5 to stay within quota.
YOUTUBE_REFRESH_INTERVAL_MINUTES=15
# SQLite database path
DATABASE_URL=sqlite+aiosqlite:///./insight.db
"""

TEXT_FILES[".gitignore"] = """\
__pycache__/
*.pyc
.env
insight.db
*.db
.venv/
venv/
.DS_Store
"""

TEXT_FILES["run.py"] = """\
#!/usr/bin/env python3
import sys, uvicorn
uvicorn.run("dashboards.server:app", host="0.0.0.0", port=8000, reload="--reload" in sys.argv)
"""

TEXT_FILES["dashboards/__init__.py"] = ""
TEXT_FILES["dashboards/youtube/__init__.py"] = ""

TEXT_FILES["dashboards/config.py"] = """\
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    database_url: str = "sqlite+aiosqlite:///./insight.db"
    youtube_api_key: str = ""
    youtube_channel_id: str = ""
    youtube_refresh_interval_minutes: int = 15

settings = Settings()
"""

TEXT_FILES["dashboards/database.py"] = """\
from contextlib import asynccontextmanager
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text, BigInteger
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from dashboards.config import settings

class Base(DeclarativeBase):
    pass

class YoutubeShort(Base):
    __tablename__ = "youtube_shorts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(String(100), nullable=False, index=True)
    video_id = Column(String(20), unique=True, nullable=False)
    title = Column(String(500))
    description = Column(Text)
    published_at = Column(DateTime)
    thumbnail_url = Column(String(1000))
    duration_seconds = Column(Integer)
    views = Column(BigInteger, default=0)
    likes = Column(BigInteger, default=0)
    comments = Column(BigInteger, default=0)
    rank = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class YoutubeViewsSnapshot(Base):
    __tablename__ = "youtube_views_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(String(100), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    total_views = Column(BigInteger, default=0)

async def init_db():
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine

@asynccontextmanager
async def get_session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
"""

TEXT_FILES["dashboards/youtube/api.py"] = """\
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import httpx

@dataclass
class ShortVideo:
    video_id: str
    title: str
    description: str
    published_at: Optional[datetime]
    thumbnail_url: str
    duration_seconds: int
    views: int
    likes: int
    comments: int

class YouTubeAPI:
    BASE = "https://www.googleapis.com/youtube/v3"
    def __init__(self, api_key: str):
        self.api_key = api_key
    async def get_channel_shorts(self, channel_id: str, max_results: int = 50):
        ids = await self._search_shorts(channel_id, max_results)
        if not ids: return []
        videos = await self._fetch_details(ids)
        shorts = [v for v in videos if v.duration_seconds <= 60]
        shorts.sort(key=lambda v: v.views, reverse=True)
        return shorts
    async def _search_shorts(self, channel_id, max_results):
        p = {"part":"id","channelId":channel_id,"type":"video","videoDuration":"short",
             "order":"viewCount","maxResults":min(max_results,50),"key":self.api_key}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{self.BASE}/search", params=p); r.raise_for_status()
        return [i["id"]["videoId"] for i in r.json().get("items",[])]
    async def _fetch_details(self, ids):
        results = []
        for i in range(0,len(ids),50):
            chunk = ids[i:i+50]
            p = {"part":"snippet,contentDetails,statistics","id":",".join(chunk),"key":self.api_key}
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(f"{self.BASE}/videos", params=p); r.raise_for_status()
            for item in r.json().get("items",[]):
                v = self._parse(item)
                if v: results.append(v)
        return results
    def _parse(self, item):
        try:
            sn = item.get("snippet",{}); st = item.get("statistics",{})
            dt = item.get("contentDetails",{})
            th = (sn.get("thumbnails",{}).get("maxres") or sn.get("thumbnails",{}).get("high") or
                  sn.get("thumbnails",{}).get("medium") or sn.get("thumbnails",{}).get("default") or {})
            pub = None
            if raw := sn.get("publishedAt"):
                try: pub = datetime.fromisoformat(raw.replace("Z","+00:00"))
                except ValueError: pass
            return ShortVideo(video_id=item["id"], title=sn.get("title",""),
                description=sn.get("description",""), published_at=pub,
                thumbnail_url=th.get("url",""),
                duration_seconds=self._iso_to_secs(dt.get("duration","PT0S")),
                views=int(st.get("viewCount",0)), likes=int(st.get("likeCount",0)),
                comments=int(st.get("commentCount",0)))
        except (KeyError, ValueError): return None
    @staticmethod
    def _iso_to_secs(d):
        m = re.match(r"PT(?:(\\d+)H)?(?:(\\d+)M)?(?:(\\d+)S)?", d)
        if not m: return 0
        return int(m.group(1) or 0)*3600 + int(m.group(2) or 0)*60 + int(m.group(3) or 0)
"""

TEXT_FILES["dashboards/youtube/cache.py"] = """\
from datetime import datetime, timedelta
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, func, delete
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from dashboards.config import settings
from dashboards.database import YoutubeShort, YoutubeViewsSnapshot, get_session
from dashboards.youtube.api import YouTubeAPI

_scheduler: Optional[AsyncIOScheduler] = None
_engine = None

async def start(engine):
    global _scheduler, _engine
    _engine = engine
    _scheduler = AsyncIOScheduler()
    interval = max(5, settings.youtube_refresh_interval_minutes)
    _scheduler.add_job(_refresh,"interval",minutes=interval,next_run_time=datetime.now(),id="yt_refresh")
    _scheduler.start()

async def stop():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)

async def refresh_now(): await _refresh()

async def _refresh():
    if not _engine: return
    api = YouTubeAPI(settings.youtube_api_key)
    cid = settings.youtube_channel_id
    try: shorts = await api.get_channel_shorts(cid, max_results=50)
    except Exception as e: print(f"[yt-cache] error: {e}"); return
    async with get_session(_engine) as session:
        for rank, s in enumerate(shorts):
            stmt = sqlite_insert(YoutubeShort).values(
                channel_id=cid, video_id=s.video_id, title=s.title,
                description=s.description, published_at=s.published_at,
                thumbnail_url=s.thumbnail_url, duration_seconds=s.duration_seconds,
                views=s.views, likes=s.likes, comments=s.comments,
                rank=rank, updated_at=datetime.utcnow()
            ).on_conflict_do_update(index_elements=["video_id"],
                set_={"title":s.title,"views":s.views,"likes":s.likes,
                      "comments":s.comments,"rank":rank,"updated_at":datetime.utcnow()})
            await session.execute(stmt)
        total = sum(s.views for s in shorts)
        session.add(YoutubeViewsSnapshot(channel_id=cid,timestamp=datetime.utcnow(),total_views=total))
        cutoff = datetime.utcnow() - timedelta(hours=48)
        await session.execute(delete(YoutubeViewsSnapshot).where(YoutubeViewsSnapshot.timestamp < cutoff))
        await session.commit()
    print(f"[yt-cache] {len(shorts)} shorts cached, total={total:,}")

async def get_shorts(limit=9):
    if not _engine: return []
    async with get_session(_engine) as session:
        res = await session.execute(select(YoutubeShort)
            .where(YoutubeShort.channel_id==settings.youtube_channel_id)
            .order_by(YoutubeShort.rank).limit(limit))
        rows = res.scalars().all()
    return [{"video_id":r.video_id,"title":r.title,"thumbnail_url":r.thumbnail_url,
             "views":r.views,"likes":r.likes,"comments":r.comments,
             "published_at":r.published_at.isoformat() if r.published_at else None} for r in rows]

async def get_metrics_7d():
    if not _engine: return {"views":0,"likes":0,"comments":0}
    cutoff = datetime.utcnow() - timedelta(days=7)
    async with get_session(_engine) as session:
        res = await session.execute(select(func.sum(YoutubeShort.views),func.sum(YoutubeShort.likes),func.sum(YoutubeShort.comments))
            .where(YoutubeShort.channel_id==settings.youtube_channel_id, YoutubeShort.published_at>=cutoff))
        row = res.one()
    return {"views":row[0] or 0,"likes":row[1] or 0,"comments":row[2] or 0}

async def get_chart_data(hours=24):
    if not _engine: return {"labels":[],"values":[]}
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    async with get_session(_engine) as session:
        res = await session.execute(select(YoutubeViewsSnapshot)
            .where(YoutubeViewsSnapshot.channel_id==settings.youtube_channel_id, YoutubeViewsSnapshot.timestamp>=cutoff)
            .order_by(YoutubeViewsSnapshot.timestamp))
        snaps = res.scalars().all()
    if len(snaps) < 2: return {"labels":[],"values":[]}
    labels, values = [], []
    for i in range(1,len(snaps)):
        delta = max(0, snaps[i].total_views - snaps[i-1].total_views)
        ts = snaps[i].timestamp
        labels.append(f"{ts.hour:02d}:{ts.minute:02d}")
        values.append(delta)
    return {"labels":labels,"values":values}
"""

TEXT_FILES["dashboards/server.py"] = """\
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from dashboards.database import init_db
import dashboards.youtube.cache as yt

FRONTEND = Path(__file__).parent.parent / "frontend"

@asynccontextmanager
async def lifespan(app):
    engine = await init_db()
    await yt.start(engine)
    yield
    await yt.stop()

app = FastAPI(title="Insight Dashboards", lifespan=lifespan)

@app.get("/api/youtube/videos")
async def api_videos(): return await yt.get_shorts(limit=9)

@app.get("/api/youtube/metrics")
async def api_metrics(): return await yt.get_metrics_7d()

@app.get("/api/youtube/chart")
async def api_chart(): return await yt.get_chart_data(hours=24)

@app.post("/api/youtube/refresh")
async def api_refresh(): await yt.refresh_now(); return {"status":"ok"}

@app.get("/", response_class=RedirectResponse)
async def root(): return RedirectResponse(url="/youtube-shorts")

@app.get("/youtube-shorts", response_class=HTMLResponse)
async def shorts_dashboard():
    return HTMLResponse((FRONTEND / "youtube-shorts" / "index.html").read_text())
"""

# ── Base64-encoded binary files (avoids escaping issues in HTML) ──────────────

INDEX_B64  = "PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImVuIj4KPGhlYWQ+CjxtZXRhIGNoYXJzZXQ9IlVURi04Ij4KPG1ldGEgbmFtZT0idmlld3BvcnQiIGNvbnRlbnQ9IndpZHRoPTEwODAiPgo8dGl0bGU+WW91VHViZSBTaG9ydHMg4oCUIExpdmU8L3RpdGxlPgo8c3R5bGU+CiAgOnJvb3QgewogICAgLS1yZWQ6ICAgICAgI0ZGMDAwMDsKICAgIC0tcmVkLWRpbTogIHJnYmEoMjU1LDAsMCwwLjE4KTsKICAgIC0tcmVkLWdsb3c6IHJnYmEoMjU1LDAsMCwwLjI4KTsKICAgIC0tYmc6ICAgICAgICMwODA4MDg7CiAgICAtLXN1cmZhY2U6ICAjMGYwZjBmOwogICAgLS1ib3JkZXI6ICAgIzFlMWUxZTsKICAgIC0tdGV4dDogICAgICNmZmZmZmY7CiAgICAtLWRpbTogICAgICAjNjA2MDYwOwogICAgLS1kaW0yOiAgICAgIzNhM2EzYTsKICB9CgogICogeyBib3gtc2l6aW5nOiBib3JkZXItYm94OyBtYXJnaW46IDA7IHBhZGRpbmc6IDA7IH0KCiAgaHRtbCwgYm9keSB7CiAgICB3aWR0aDogMTA4MHB4OwogICAgaGVpZ2h0OiAxOTIwcHg7CiAgICBvdmVyZmxvdzogaGlkZGVuOwogICAgYmFja2dyb3VuZDogdmFyKC0tYmcpOwogICAgY29sb3I6IHZhcigtLXRleHQpOwogICAgZm9udC1mYW1pbHk6IC1hcHBsZS1zeXN0ZW0sIEJsaW5rTWFjU3lzdGVtRm9udCwgIkhlbHZldGljYSBOZXVlIiwgQXJpYWwsIHNhbnMtc2VyaWY7CiAgICAtd2Via2l0LWZvbnQtc21vb3RoaW5nOiBhbnRpYWxpYXNlZDsKICB9CgogIGJvZHkgeyBkaXNwbGF5OiBmbGV4OyBmbGV4LWRpcmVjdGlvbjogY29sdW1uOyB9CgogIC8qIOKUgOKUgCBIZWFkZXIg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAICovCiAgI2hlYWRlciB7CiAgICBmbGV4OiAwIDAgMTkycHg7CiAgICBkaXNwbGF5OiBmbGV4OwogICAgZmxleC1kaXJlY3Rpb246IGNvbHVtbjsKICAgIGp1c3RpZnktY29udGVudDogY2VudGVyOwogICAgcGFkZGluZzogMCAzNnB4OwogICAgYmFja2dyb3VuZDogdmFyKC0tc3VyZmFjZSk7CiAgICBib3JkZXItYm90dG9tOiAxcHggc29saWQgdmFyKC0tYm9yZGVyKTsKICAgIGdhcDogMThweDsKICB9CgogICNoZWFkZXItdG9wIHsKICAgIGRpc3BsYXk6IGZsZXg7CiAgICBhbGlnbi1pdGVtczogY2VudGVyOwogICAganVzdGlmeS1jb250ZW50OiBzcGFjZS1iZXR3ZWVuOwogIH0KCiAgI2NoYW5uZWwtaWQgewogICAgZGlzcGxheTogZmxleDsKICAgIGFsaWduLWl0ZW1zOiBjZW50ZXI7CiAgICBnYXA6IDEycHg7CiAgfQoKICAjY2hhbm5lbC1pY29uIHsKICAgIHdpZHRoOiA0NHB4OyBoZWlnaHQ6IDQ0cHg7CiAgICBiYWNrZ3JvdW5kOiB2YXIoLS1yZWQpOwogICAgYm9yZGVyLXJhZGl1czogNTAlOwogICAgZGlzcGxheTogZmxleDsgYWxpZ24taXRlbXM6IGNlbnRlcjsganVzdGlmeS1jb250ZW50OiBjZW50ZXI7CiAgICBmbGV4LXNocmluazogMDsKICB9CiAgI2NoYW5uZWwtaWNvbiBzdmcgeyB3aWR0aDogMjJweDsgaGVpZ2h0OiAyMnB4OyBmaWxsOiAjZmZmOyB9CgogICNjaGFubmVsLW5hbWUgeyBmb250LXNpemU6IDE1cHg7IGZvbnQtd2VpZ2h0OiA2MDA7IGxldHRlci1zcGFjaW5nOiAtMC4ycHg7IH0KICAjY2hhbm5lbC1oYW5kbGUgeyBmb250LXNpemU6IDEycHg7IGNvbG9yOiB2YXIoLS1kaW0pOyBtYXJnaW4tdG9wOiAxcHg7IH0KCiAgI3l0LWJhZGdlIHsKICAgIGRpc3BsYXk6IGZsZXg7IGFsaWduLWl0ZW1zOiBjZW50ZXI7IGdhcDogN3B4OwogICAgcGFkZGluZzogNnB4IDE0cHg7CiAgICBib3JkZXI6IDFweCBzb2xpZCB2YXIoLS1ib3JkZXIpOwogICAgYm9yZGVyLXJhZGl1czogNnB4OwogIH0KICAjeXQtYmFkZ2Ugc3ZnIHsgd2lkdGg6IDIycHg7IGhlaWdodDogMTZweDsgfQogICN5dC1iYWRnZSBzcGFuIHsgZm9udC1zaXplOiAxMXB4OyBjb2xvcjogdmFyKC0tZGltKTsgbGV0dGVyLXNwYWNpbmc6IDAuNXB4OyB0ZXh0LXRyYW5zZm9ybTogdXBwZXJjYXNlOyB9CgogIC8qIFN0YXQgcm93ICovCiAgI3N0YXQtcm93IHsgZGlzcGxheTogZmxleDsgYWxpZ24taXRlbXM6IHN0cmV0Y2g7IH0KCiAgLnN0YXQgewogICAgZGlzcGxheTogZmxleDsKICAgIGFsaWduLWl0ZW1zOiBjZW50ZXI7CiAgICBnYXA6IDE0cHg7CiAgICBwYWRkaW5nLXJpZ2h0OiAzNnB4OwogICAgbWFyZ2luLXJpZ2h0OiAzNnB4OwogICAgYm9yZGVyLXJpZ2h0OiAxcHggc29saWQgdmFyKC0tYm9yZGVyKTsKICB9CiAgLnN0YXQ6bGFzdC1jaGlsZCB7IGJvcmRlci1yaWdodDogbm9uZTsgcGFkZGluZy1yaWdodDogMDsgbWFyZ2luLXJpZ2h0OiAwOyB9CgogIC5zdGF0LWljb24gewogICAgd2lkdGg6IDQwcHg7IGhlaWdodDogNDBweDsKICAgIGJhY2tncm91bmQ6IHZhcigtLXJlZC1kaW0pOwogICAgYm9yZGVyLXJhZGl1czogMTBweDsKICAgIGRpc3BsYXk6IGZsZXg7IGFsaWduLWl0ZW1zOiBjZW50ZXI7IGp1c3RpZnktY29udGVudDogY2VudGVyOwogICAgZmxleC1zaHJpbms6IDA7CiAgfQogIC5zdGF0LWljb24gc3ZnIHsgd2lkdGg6IDIwcHg7IGhlaWdodDogMjBweDsgZmlsbDogdmFyKC0tcmVkKTsgfQoKICAuc3RhdC1ib2R5IHsgZGlzcGxheTogZmxleDsgZmxleC1kaXJlY3Rpb246IGNvbHVtbjsgfQoKICAuc3RhdC12YWx1ZSB7CiAgICBmb250LXNpemU6IDQycHg7CiAgICBmb250LXdlaWdodDogODAwOwogICAgbGV0dGVyLXNwYWNpbmc6IC0ycHg7CiAgICBsaW5lLWhlaWdodDogMTsKICAgIGZvbnQtdmFyaWFudC1udW1lcmljOiB0YWJ1bGFyLW51bXM7CiAgfQogIC5zdGF0LXZhbHVlLmFjY2VudCB7IGNvbG9yOiB2YXIoLS1yZWQpOyB9CgogIC5zdGF0LW1ldGEgewogICAgZm9udC1zaXplOiAxMHB4OwogICAgY29sb3I6IHZhcigtLWRpbSk7CiAgICB0ZXh0LXRyYW5zZm9ybTogdXBwZXJjYXNlOwogICAgbGV0dGVyLXNwYWNpbmc6IDEuMnB4OwogICAgbWFyZ2luLXRvcDogNHB4OwogIH0KCiAgLyog4pSA4pSAIFNlY3Rpb24gbGFiZWwg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAICovCiAgI3NlY3Rpb24tbGFiZWwgewogICAgZmxleDogMCAwIDM4cHg7CiAgICBkaXNwbGF5OiBmbGV4OwogICAgYWxpZ24taXRlbXM6IGNlbnRlcjsKICAgIGp1c3RpZnktY29udGVudDogc3BhY2UtYmV0d2VlbjsKICAgIHBhZGRpbmc6IDAgMzZweDsKICAgIGJvcmRlci1ib3R0b206IDFweCBzb2xpZCB2YXIoLS1ib3JkZXIpOwogIH0KCiAgI3NlY3Rpb24tdGl0bGUgewogICAgZm9udC1zaXplOiAxMHB4OwogICAgZm9udC13ZWlnaHQ6IDcwMDsKICAgIGNvbG9yOiB2YXIoLS1kaW0pOwogICAgdGV4dC10cmFuc2Zvcm06IHVwcGVyY2FzZTsKICAgIGxldHRlci1zcGFjaW5nOiAycHg7CiAgfQoKICAjcmVmcmVzaC1pbmZvIHsKICAgIGZvbnQtc2l6ZTogMTBweDsKICAgIGNvbG9yOiB2YXIoLS1kaW0yKTsKICB9CiAgI3JlZnJlc2gtaW5mbyBzcGFuIHsgY29sb3I6IHZhcigtLWRpbSk7IH0KCiAgLyog4pSA4pSAIEdyaWQg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAICovCiAgI2dyaWQgewogICAgZmxleDogMTsKICAgIGRpc3BsYXk6IGdyaWQ7CiAgICBncmlkLXRlbXBsYXRlLWNvbHVtbnM6IHJlcGVhdCgzLCAxZnIpOwogICAgZ3JpZC10ZW1wbGF0ZS1yb3dzOiByZXBlYXQoMywgMWZyKTsKICAgIGdhcDogMnB4OwogICAgYmFja2dyb3VuZDogIzAwMDsKICB9CgogIC5jYXJkIHsKICAgIHBvc2l0aW9uOiByZWxhdGl2ZTsKICAgIG92ZXJmbG93OiBoaWRkZW47CiAgICBiYWNrZ3JvdW5kOiAjMTExOwogICAgdHJhbnNpdGlvbjogYm94LXNoYWRvdyAwLjI1cyBlYXNlOwogIH0KCiAgLmNhcmQucGxheWluZyB7CiAgICBib3gtc2hhZG93OiBpbnNldCAwIDAgMCAycHggdmFyKC0tcmVkKTsKICAgIHotaW5kZXg6IDI7CiAgfQoKICAuY2FyZC5wbGF5aW5nOjpiZWZvcmUgewogICAgY29udGVudDogIiI7CiAgICBwb3NpdGlvbjogYWJzb2x1dGU7CiAgICBpbnNldDogMDsKICAgIGJveC1zaGFkb3c6IGluc2V0IDAgMCA0MHB4IHJnYmEoMjU1LDAsMCwwLjE1KTsKICAgIHotaW5kZXg6IDM7CiAgICBwb2ludGVyLWV2ZW50czogbm9uZTsKICB9CgogIC5jYXJkLXRodW1iIHsKICAgIHBvc2l0aW9uOiBhYnNvbHV0ZTsKICAgIGluc2V0OiAwOwogICAgd2lkdGg6IDEwMCU7CiAgICBoZWlnaHQ6IDEwMCU7CiAgICBvYmplY3QtZml0OiBjb3ZlcjsKICB9CgogIC5jYXJkLXBsYXllciB7CiAgICBwb3NpdGlvbjogYWJzb2x1dGU7CiAgICBpbnNldDogLTEwJSAtMTAlOwogICAgd2lkdGg6IDEyMCU7CiAgICBoZWlnaHQ6IDEyMCU7CiAgICBwb2ludGVyLWV2ZW50czogbm9uZTsKICAgIG9wYWNpdHk6IDA7CiAgICB0cmFuc2l0aW9uOiBvcGFjaXR5IDAuNHMgZWFzZTsKICAgIGJhY2tncm91bmQ6ICMwMDA7CiAgICB6LWluZGV4OiAxOwogIH0KICAuY2FyZC5wbGF5aW5nIC5jYXJkLXBsYXllciB7IG9wYWNpdHk6IDE7IH0KCiAgLmNhcmQtb3ZlcmxheSB7CiAgICBwb3NpdGlvbjogYWJzb2x1dGU7CiAgICBib3R0b206IDA7IGxlZnQ6IDA7IHJpZ2h0OiAwOwogICAgcGFkZGluZzogMjhweCAxNHB4IDEycHg7CiAgICBiYWNrZ3JvdW5kOiBsaW5lYXItZ3JhZGllbnQodG8gdG9wLCByZ2JhKDAsMCwwLDAuOTIpIDAlLCByZ2JhKDAsMCwwLDAuNSkgNjAlLCB0cmFuc3BhcmVudCAxMDAlKTsKICAgIHotaW5kZXg6IDI7CiAgfQoKICAuY2FyZC1zdGF0cyB7CiAgICBkaXNwbGF5OiBmbGV4OwogICAgYWxpZ24taXRlbXM6IGNlbnRlcjsKICAgIGdhcDogMTRweDsKICB9CgogIC5jcyB7CiAgICBkaXNwbGF5OiBmbGV4OwogICAgYWxpZ24taXRlbXM6IGNlbnRlcjsKICAgIGdhcDogNXB4OwogICAgZm9udC1zaXplOiAxM3B4OwogICAgZm9udC13ZWlnaHQ6IDcwMDsKICAgIGNvbG9yOiAjZmZmOwogICAgbGV0dGVyLXNwYWNpbmc6IC0wLjNweDsKICB9CiAgLmNzIHN2ZyB7IHdpZHRoOiAxMnB4OyBoZWlnaHQ6IDEycHg7IGZpbGw6IHJnYmEoMjU1LDI1NSwyNTUsMC41NSk7IGZsZXgtc2hyaW5rOiAwOyB9CgogIC5jYXJkLXJhbmsgewogICAgcG9zaXRpb246IGFic29sdXRlOwogICAgdG9wOiAxMHB4OyBsZWZ0OiAxMHB4OwogICAgei1pbmRleDogMzsKICAgIGZvbnQtc2l6ZTogMTFweDsKICAgIGZvbnQtd2VpZ2h0OiA4MDA7CiAgICBjb2xvcjogcmdiYSgyNTUsMjU1LDI1NSwwLjkpOwogICAgYmFja2dyb3VuZDogcmdiYSgwLDAsMCwwLjU1KTsKICAgIGJvcmRlcjogMXB4IHNvbGlkIHJnYmEoMjU1LDI1NSwyNTUsMC4xKTsKICAgIHdpZHRoOiAyNHB4OyBoZWlnaHQ6IDI0cHg7CiAgICBib3JkZXItcmFkaXVzOiA1MCU7CiAgICBkaXNwbGF5OiBmbGV4OyBhbGlnbi1pdGVtczogY2VudGVyOyBqdXN0aWZ5LWNvbnRlbnQ6IGNlbnRlcjsKICAgIGJhY2tkcm9wLWZpbHRlcjogYmx1cig0cHgpOwogIH0KCiAgLmNhcmQtbGl2ZSB7CiAgICBkaXNwbGF5OiBub25lOwogICAgcG9zaXRpb246IGFic29sdXRlOwogICAgdG9wOiAxMHB4OyByaWdodDogMTBweDsKICAgIHotaW5kZXg6IDM7CiAgICBhbGlnbi1pdGVtczogY2VudGVyOwogICAgZ2FwOiA1cHg7CiAgICBiYWNrZ3JvdW5kOiB2YXIoLS1yZWQpOwogICAgcGFkZGluZzogM3B4IDlweDsKICAgIGJvcmRlci1yYWRpdXM6IDRweDsKICAgIGZvbnQtc2l6ZTogOXB4OwogICAgZm9udC13ZWlnaHQ6IDgwMDsKICAgIGxldHRlci1zcGFjaW5nOiAwLjhweDsKICB9CiAgLmNhcmQucGxheWluZyAuY2FyZC1saXZlIHsKICAgIGRpc3BsYXk6IGZsZXg7CiAgICBhbmltYXRpb246IGJsaW5rIDEuOHMgZWFzZS1pbi1vdXQgaW5maW5pdGU7CiAgfQogIC5jYXJkLWxpdmU6OmJlZm9yZSB7CiAgICBjb250ZW50OiAiIjsKICAgIHdpZHRoOiA1cHg7IGhlaWdodDogNXB4OwogICAgYm9yZGVyLXJhZGl1czogNTAlOwogICAgYmFja2dyb3VuZDogI2ZmZjsKICB9CiAgQGtleWZyYW1lcyBibGluayB7IDAlLDEwMCV7b3BhY2l0eToxfSA1MCV7b3BhY2l0eTowLjR9IH0KCiAgLyog4pSA4pSAIENoYXJ0IHNlY3Rpb24g4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAICovCiAgI2NoYXJ0LXNlY3Rpb24gewogICAgZmxleDogMCAwIDI2NHB4OwogICAgYmFja2dyb3VuZDogdmFyKC0tc3VyZmFjZSk7CiAgICBib3JkZXItdG9wOiAxcHggc29saWQgdmFyKC0tYm9yZGVyKTsKICAgIHBhZGRpbmc6IDE4cHggMjhweCAxNHB4OwogICAgZGlzcGxheTogZmxleDsKICAgIGZsZXgtZGlyZWN0aW9uOiBjb2x1bW47CiAgICBnYXA6IDEwcHg7CiAgfQoKICAjY2hhcnQtaGVhZGVyIHsKICAgIGRpc3BsYXk6IGZsZXg7CiAgICBhbGlnbi1pdGVtczogY2VudGVyOwogICAganVzdGlmeS1jb250ZW50OiBzcGFjZS1iZXR3ZWVuOwogIH0KCiAgI2NoYXJ0LWxhYmVsIHsKICAgIGZvbnQtc2l6ZTogMTBweDsKICAgIGZvbnQtd2VpZ2h0OiA3MDA7CiAgICBjb2xvcjogdmFyKC0tZGltKTsKICAgIHRleHQtdHJhbnNmb3JtOiB1cHBlcmNhc2U7CiAgICBsZXR0ZXItc3BhY2luZzogMnB4OwogIH0KCiAgI2NoYXJ0LXBlYWsgeyBmb250LXNpemU6IDEwcHg7IGNvbG9yOiB2YXIoLS1kaW0yKTsgfQogICNjaGFydC1wZWFrIHN0cm9uZyB7IGNvbG9yOiB2YXIoLS1kaW0pOyBmb250LXdlaWdodDogNjAwOyB9CgogICNjaGFydC13cmFwIHsgZmxleDogMTsgcG9zaXRpb246IHJlbGF0aXZlOyB9CiAgY2FudmFzIHsgZGlzcGxheTogYmxvY2s7IH0KPC9zdHlsZT4KPC9oZWFkPgo8Ym9keT4KCjxkaXYgaWQ9ImhlYWRlciI+CiAgPGRpdiBpZD0iaGVhZGVyLXRvcCI+CiAgICA8ZGl2IGlkPSJjaGFubmVsLWlkIj4KICAgICAgPGRpdiBpZD0iY2hhbm5lbC1pY29uIj4KICAgICAgICA8c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZD0iTTEwIDE1LjVsNi0zLjUtNi0zLjV2N3oiLz48L3N2Zz4KICAgICAgPC9kaXY+CiAgICAgIDxkaXY+CiAgICAgICAgPGRpdiBpZD0iY2hhbm5lbC1uYW1lIj5Mb2FkaW5n4oCmPC9kaXY+CiAgICAgICAgPGRpdiBpZD0iY2hhbm5lbC1oYW5kbGUiPkBjaGFubmVsIMK3IFNob3J0czwvZGl2PgogICAgICA8L2Rpdj4KICAgIDwvZGl2PgogICAgPGRpdiBpZD0ieXQtYmFkZ2UiPgogICAgICA8c3ZnIHZpZXdCb3g9IjAgMCA5MCAyMCIgZmlsbD0ibm9uZSI+CiAgICAgICAgPHBhdGggZD0iTTI3LjkgMy41YTMuNSAzLjUgMCAwIDAtMi41LTIuNUMyMy4yLjUgMTQuNS41IDE0LjUuNVM1LjguNSAzLjYgMWMtMS4yLjMtMi4yIDEuMy0yLjUgMi41Qy42IDUuNy42IDEwIC42IDEwczAgNC4zLjUgNi41Yy4zIDEuMiAxLjMgMi4yIDIuNSAyLjUgMi4yLjYgMTAuOS42IDEwLjkuNnM4LjcgMCAxMC45LS42YzEuMi0uMyAyLjItMS4zIDIuNS0yLjUuNS0yLjIuNS02LjUuNS02LjVzMC00LjMtLjUtNi41eiIgZmlsbD0iI0ZGMDAwMCIvPgogICAgICAgIDxwYXRoIGQ9Ik0xMS44IDE0bDcuMi00LTcuMi00djh6IiBmaWxsPSIjZmZmIi8+CiAgICAgIDwvc3ZnPgogICAgICA8c3Bhbj5MaXZlPC9zcGFuPgogICAgPC9kaXY+CiAgPC9kaXY+CgogIDxkaXYgaWQ9InN0YXQtcm93Ij4KICAgIDxkaXYgY2xhc3M9InN0YXQiPgogICAgICA8ZGl2IGNsYXNzPSJzdGF0LWljb24iPgogICAgICAgIDxzdmcgdmlld0JveD0iMCAwIDI0IDI0Ij48cGF0aCBkPSJNMTIgNC41QzcgNC41IDIuNzMgNy42MSAxIDEyYzEuNzMgNC4zOSA2IDcuNSAxMSA3LjVzOS4yNy0zLjExIDExLTcuNWMtMS43My00LjM5LTYtNy41LTExLTcuNXptMCAxMi41YTUgNSAwIDEgMSAwLTEwIDUgNSAwIDAgMSAwIDEwem0wLThhMyAzIDAgMSAwIDAgNiAzIDMgMCAwIDAgMC02eiIvPjwvc3ZnPgogICAgICA8L2Rpdj4KICAgICAgPGRpdiBjbGFzcz0ic3RhdC1ib2R5Ij4KICAgICAgICA8ZGl2IGNsYXNzPSJzdGF0LXZhbHVlIGFjY2VudCIgaWQ9InN0YXQtdmlld3MiPuKAlDwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9InN0YXQtbWV0YSI+Vmlld3MgwrcgNyBkYXlzPC9kaXY+CiAgICAgIDwvZGl2PgogICAgPC9kaXY+CiAgICA8ZGl2IGNsYXNzPSJzdGF0Ij4KICAgICAgPGRpdiBjbGFzcz0ic3RhdC1pY29uIj4KICAgICAgICA8c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZD0iTTEgMjFoNFY5SDF2MTJ6bTIyLTExYzAtMS4xLS45LTItMi0yaC02LjMxbC45NS00LjU3LjAzLS4zMmMwLS40MS0uMTctLjc5LS40NC0xLjA2TDE0LjE3IDEgNy41OSA3LjU5QzcuMjIgNy45NSA3IDl2MTBjMCAxLjEuOSAyIDIgMmg5Yy44MyAwIDEuNTQtLjUgMS44NC0xLjIybDMuMDItNy4wNWMuMDktLjIzLjE0LS40Ny4xNC0uNzN2LTJ6Ii8+PC9zdmc+CiAgICAgIDwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGF0LWJvZHkiPgogICAgICAgIDxkaXYgY2xhc3M9InN0YXQtdmFsdWUiIGlkPSJzdGF0LWxpa2VzIj7igJQ8L2Rpdj4KICAgICAgICA8ZGl2IGNsYXNzPSJzdGF0LW1ldGEiPkxpa2VzIMK3IDcgZGF5czwvZGl2PgogICAgICA8L2Rpdj4KICAgIDwvZGl2PgogICAgPGRpdiBjbGFzcz0ic3RhdCI+CiAgICAgIDxkaXYgY2xhc3M9InN0YXQtaWNvbiI+CiAgICAgICAgPHN2ZyB2aWV3Qm94PSIwIDAgMjQgMjQiPjxwYXRoIGQ9Ik0yMSA2aC0ydjlINnYyYzAgLjU1LjQ1IDEgMSAxaDExbDQgNFY3YzAtLjU1LS40NS0xLTEtMXptLTQgNlYzYzAtLjU1LS40NS0xLTEtMUgzYy0uNTUgMC0xIC40NS0xIDF2MTRsNC00aDEwYy41NSAwIDEtLjQ1IDEtMXoiLz48L3N2Zz4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0YXQtYm9keSI+CiAgICAgICAgPGRpdiBjbGFzcz0ic3RhdC12YWx1ZSIgaWQ9InN0YXQtY29tbWVudHMiPuKAlDwvZGl2PgogICAgICAgIDxkaXYgY2xhc3M9InN0YXQtbWV0YSI+Q29tbWVudHMgwrcgNyBkYXlzPC9kaXY+CiAgICAgIDwvZGl2PgogICAgPC9kaXY+CiAgPC9kaXY+CjwvZGl2PgoKPGRpdiBpZD0ic2VjdGlvbi1sYWJlbCI+CiAgPHNwYW4gaWQ9InNlY3Rpb24tdGl0bGUiPlRvcCBTaG9ydHMgwrcgTGFzdCA3IERheXM8L3NwYW4+CiAgPHNwYW4gaWQ9InJlZnJlc2gtaW5mbyI+TmV4dCByZWZyZXNoIGluIDxzcGFuIGlkPSJjb3VudGRvd24iPuKAlDwvc3Bhbj48L3NwYW4+CjwvZGl2PgoKPGRpdiBpZD0iZ3JpZCI+PC9kaXY+Cgo8ZGl2IGlkPSJjaGFydC1zZWN0aW9uIj4KICA8ZGl2IGlkPSJjaGFydC1oZWFkZXIiPgogICAgPHNwYW4gaWQ9ImNoYXJ0LWxhYmVsIj5WaWV3cyDCtyBMYXN0IDI0IEhvdXJzPC9zcGFuPgogICAgPHNwYW4gaWQ9ImNoYXJ0LXBlYWsiPlBlYWsgPHN0cm9uZyBpZD0icGVhay12YWwiPuKAlDwvc3Ryb25nPjwvc3Bhbj4KICA8L2Rpdj4KICA8ZGl2IGlkPSJjaGFydC13cmFwIj4KICAgIDxjYW52YXMgaWQ9ImNoYXJ0Ij48L2NhbnZhcz4KICA8L2Rpdj4KPC9kaXY+Cgo8c2NyaXB0IHNyYz0iaHR0cHM6Ly9jZG4uanNkZWxpdnIubmV0L25wbS9jaGFydC5qc0A0LjQuMi9kaXN0L2NoYXJ0LnVtZC5taW4uanMiPjwvc2NyaXB0Pgo8c2NyaXB0Pgpjb25zdCBSRUZSRVNIX01TICA9IDE1ICogNjAgKiAxMDAwOwpjb25zdCBIT0xEX1NFQ1MgICA9IDIwOwpjb25zdCBDT1VOVFVQX01TICA9IDI4MDA7CgpsZXQgdmlkZW9zID0gW10sIGFjdGl2ZUlkeCA9IDAsIHBsYXllcnMgPSB7fSwgaG9sZFRpbWVyID0gbnVsbDsKbGV0IGNoYXJ0ID0gbnVsbCwgcmVmcmVzaFRpbWVyID0gbnVsbCwgbmV4dFJlZnJlc2hBdCA9IG51bGw7CgovLyDilIDilIAgWW91VHViZSBJRnJhbWUgQVBJIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgAooZnVuY3Rpb24oKXsgY29uc3Qgcz1kb2N1bWVudC5jcmVhdGVFbGVtZW50KCJzY3JpcHQiKTsgcy5zcmM9Imh0dHBzOi8vd3d3LnlvdXR1YmUuY29tL2lmcmFtZV9hcGkiOyBkb2N1bWVudC5oZWFkLmFwcGVuZENoaWxkKHMpOyB9KSgpOwoKZnVuY3Rpb24gb25Zb3VUdWJlSWZyYW1lQVBJUmVhZHkoKXsKICBpZih2aWRlb3MubGVuZ3RoKSByZW5kZXJQbGF5ZXJzKCk7Cn0KCi8vIOKUgOKUgCBGZXRjaCArIHJlZnJlc2gg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmFzeW5jIGZ1bmN0aW9uIHJlZnJlc2goKXsKICB0cnkgewogICAgY29uc3QgW3ZSZXMsbVJlcyxjUmVzXSA9IGF3YWl0IFByb21pc2UuYWxsKFsKICAgICAgZmV0Y2goIi9hcGkveW91dHViZS92aWRlb3MiKSwKICAgICAgZmV0Y2goIi9hcGkveW91dHViZS9tZXRyaWNzIiksCiAgICAgIGZldGNoKCIvYXBpL3lvdXR1YmUvY2hhcnQiKSwKICAgIF0pOwogICAgY29uc3QgW3ZpZHMsbWV0cmljcyxjaGFydERhdGFdID0gYXdhaXQgUHJvbWlzZS5hbGwoW3ZSZXMuanNvbigpLG1SZXMuanNvbigpLGNSZXMuanNvbigpXSk7CiAgICB2aWRlb3MgPSB2aWRzOwogICAgYXBwbHlTdGF0cyhtZXRyaWNzKTsKICAgIGJ1aWxkR3JpZCh2aWRzKTsKICAgIGJ1aWxkQ2hhcnQoY2hhcnREYXRhKTsKICB9IGNhdGNoKGUpeyBjb25zb2xlLmVycm9yKCJyZWZyZXNoOiIsZSk7IH0KICBzY2hlZHVsZU5leHQoKTsKfQoKZnVuY3Rpb24gc2NoZWR1bGVOZXh0KCl7CiAgY2xlYXJUaW1lb3V0KHJlZnJlc2hUaW1lcik7CiAgbmV4dFJlZnJlc2hBdCA9IERhdGUubm93KCkgKyBSRUZSRVNIX01TOwogIHJlZnJlc2hUaW1lciAgPSBzZXRUaW1lb3V0KHJlZnJlc2gsIFJFRlJFU0hfTVMpOwp9CgovLyDilIDilIAgQ291bnRkb3duIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApzZXRJbnRlcnZhbCgoKT0+ewogIGlmKCFuZXh0UmVmcmVzaEF0KSByZXR1cm47CiAgY29uc3Qgcz1NYXRoLm1heCgwLE1hdGgucm91bmQoKG5leHRSZWZyZXNoQXQtRGF0ZS5ub3coKSkvMTAwMCkpOwogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjb3VudGRvd24iKS50ZXh0Q29udGVudD0KICAgIGAke1N0cmluZyhNYXRoLmZsb29yKHMvNjApKS5wYWRTdGFydCgyLCIwIil9OiR7U3RyaW5nKHMlNjApLnBhZFN0YXJ0KDIsIjAiKX1gOwp9LDEwMDApOwoKLy8g4pSA4pSAIEZvcm1hdHRpbmcg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmZ1bmN0aW9uIGZtdChuKXsKICBpZihuPj0xZTYpIHJldHVybihuLzFlNikudG9GaXhlZChuPj0xMGU2PzE6MikrIk0iOwogIGlmKG4+PTFlMykgcmV0dXJuKG4vMWUzKS50b0ZpeGVkKG4+PTEwMGUzPzA6MSkrIksiOwogIHJldHVybiBuLnRvTG9jYWxlU3RyaW5nKCk7Cn0KCmZ1bmN0aW9uIGNvdW50VXAoZWwsdGFyZ2V0KXsKICBjb25zdCBzdGVwcz04MDsKICBsZXQgc3RlcD0wOwogIGNvbnN0IHQ9c2V0SW50ZXJ2YWwoKCk9PnsKICAgIHN0ZXArKzsKICAgIGNvbnN0IHA9c3RlcC9zdGVwcywgZWFzZT1wPDAuNT8yKnAqcDotMSsoNC0yKnApKnA7CiAgICBlbC50ZXh0Q29udGVudD1mbXQoTWF0aC5yb3VuZCh0YXJnZXQqZWFzZSkpOwogICAgaWYoc3RlcD49c3RlcHMpe2NsZWFySW50ZXJ2YWwodCk7ZWwudGV4dENvbnRlbnQ9Zm10KHRhcmdldCk7fQogIH0sQ09VTlRVUF9NUy9zdGVwcyk7Cn0KCmZ1bmN0aW9uIGFwcGx5U3RhdHMobSl7CiAgY291bnRVcChkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3RhdC12aWV3cyIpLCAgICBtLnZpZXdzKTsKICBjb3VudFVwKGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJzdGF0LWxpa2VzIiksICAgIG0ubGlrZXMpOwogIGNvdW50VXAoZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInN0YXQtY29tbWVudHMiKSwgbS5jb21tZW50cyk7Cn0KCi8vIOKUgOKUgCBHcmlkIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApjb25zdCBFWUUgID0gYDxzdmcgdmlld0JveD0iMCAwIDI0IDI0Ij48cGF0aCBkPSJNMTIgNC41QzcgNC41IDIuNzMgNy42MSAxIDEyYzEuNzMgNC4zOSA2IDcuNSAxMSA3LjVzOS4yNy0zLjExIDExLTcuNWMtMS43My00LjM5LTYtNy41LTExLTcuNXptMCAxMi41YTUgNSAwIDEgMSAwLTEwIDUgNSAwIDAgMSAwIDEwem0wLThhMyAzIDAgMSAwIDAgNiAzIDMgMCAwIDAgMC02eiIvPjwvc3ZnPmA7CmNvbnN0IExJS0UgPSBgPHN2ZyB2aWV3Qm94PSIwIDAgMjQgMjQiPjxwYXRoIGQ9Ik0xIDIxaDRWOUgxdjEyem0yMi0xMWMwLTEuMS0uOS0yLTItMmgtNi4zMWwuOTUtNC41Ny4wMy0uMzJjMC0uNDEtLjE3LS43OS0uNDQtMS4wNkwxNC4xNyAxIDcuNTkgNy41OUM3LjIyIDcuOTUgNyA5djEwYzAgMS4xLjkgMiAyIDJoOWMuODMgMCAxLjU0LS41IDEuODQtMS4yMmwzLjAyLTcuMDVjLjA5LS4yMy4xNC0uNDcuMTQtLjczdi0yeiIvPjwvc3ZnPmA7CgpmdW5jdGlvbiBidWlsZEdyaWQodmlkcyl7CiAgY29uc3QgZz1kb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiZ3JpZCIpOwoKICAvLyBSZW1vdmUgc3RhbGUgY2FyZHMKICBjb25zdCBuZXdJZHM9bmV3IFNldCh2aWRzLm1hcCh2PT52LnZpZGVvX2lkKSk7CiAgWy4uLmcucXVlcnlTZWxlY3RvckFsbCgiLmNhcmQiKV0uZm9yRWFjaChjPT57CiAgICBpZighbmV3SWRzLmhhcyhjLmRhdGFzZXQudmlkKSl7IHBsYXllcnNbYy5kYXRhc2V0LnZpZF0/LmRlc3Ryb3koKTsgZGVsZXRlIHBsYXllcnNbYy5kYXRhc2V0LnZpZF07IGMucmVtb3ZlKCk7IH0KICB9KTsKCiAgdmlkcy5mb3JFYWNoKCh2LGkpPT57CiAgICBsZXQgY2FyZD1nLnF1ZXJ5U2VsZWN0b3IoYFtkYXRhLXZpZD0iJHt2LnZpZGVvX2lkfSJdYCk7CiAgICBpZighY2FyZCl7CiAgICAgIGNhcmQ9ZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgiZGl2Iik7CiAgICAgIGNhcmQuY2xhc3NOYW1lPSJjYXJkIjsKICAgICAgY2FyZC5kYXRhc2V0LnZpZD12LnZpZGVvX2lkOwoKICAgICAgY29uc3QgdGh1bWI9ZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgiaW1nIik7CiAgICAgIHRodW1iLmNsYXNzTmFtZT0iY2FyZC10aHVtYiI7CiAgICAgIHRodW1iLnNyYz12LnRodW1ibmFpbF91cmw7CiAgICAgIHRodW1iLmFsdD0iIjsKCiAgICAgIGNvbnN0IHBsYXllckRpdj1kb2N1bWVudC5jcmVhdGVFbGVtZW50KCJkaXYiKTsKICAgICAgcGxheWVyRGl2LmNsYXNzTmFtZT0iY2FyZC1wbGF5ZXIiOwogICAgICBwbGF5ZXJEaXYuaWQ9YHBsLSR7di52aWRlb19pZH1gOwoKICAgICAgY29uc3Qgb3ZlcmxheT1kb2N1bWVudC5jcmVhdGVFbGVtZW50KCJkaXYiKTsKICAgICAgb3ZlcmxheS5jbGFzc05hbWU9ImNhcmQtb3ZlcmxheSI7CgogICAgICBjb25zdCBzdGF0cz1kb2N1bWVudC5jcmVhdGVFbGVtZW50KCJkaXYiKTsKICAgICAgc3RhdHMuY2xhc3NOYW1lPSJjYXJkLXN0YXRzIjsKICAgICAgc3RhdHMuaW5uZXJIVE1MPWA8c3BhbiBjbGFzcz0iY3MiPiR7RVlFfSR7Zm10KHYudmlld3MpfTwvc3Bhbj48c3BhbiBjbGFzcz0iY3MiPiR7TElLRX0ke2ZtdCh2Lmxpa2VzKX08L3NwYW4+YDsKICAgICAgb3ZlcmxheS5hcHBlbmRDaGlsZChzdGF0cyk7CgogICAgICBjb25zdCByYW5rPWRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoImRpdiIpOwogICAgICByYW5rLmNsYXNzTmFtZT0iY2FyZC1yYW5rIjsKICAgICAgcmFuay50ZXh0Q29udGVudD1pKzE7CgogICAgICBjb25zdCBsaXZlPWRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoImRpdiIpOwogICAgICBsaXZlLmNsYXNzTmFtZT0iY2FyZC1saXZlIjsKICAgICAgbGl2ZS50ZXh0Q29udGVudD0iTElWRSI7CgogICAgICBjYXJkLmFwcGVuZCh0aHVtYixwbGF5ZXJEaXYsb3ZlcmxheSxyYW5rLGxpdmUpOwogICAgICBnLmFwcGVuZENoaWxkKGNhcmQpOwogICAgfSBlbHNlIHsKICAgICAgLy8gUmVmcmVzaCBzdGF0cyBvbmx5CiAgICAgIGNvbnN0IHN0YXRzPWNhcmQucXVlcnlTZWxlY3RvcigiLmNhcmQtc3RhdHMiKTsKICAgICAgaWYoc3RhdHMpIHN0YXRzLmlubmVySFRNTD1gPHNwYW4gY2xhc3M9ImNzIj4ke0VZRX0ke2ZtdCh2LnZpZXdzKX08L3NwYW4+PHNwYW4gY2xhc3M9ImNzIj4ke0xJS0V9JHtmbXQodi5saWtlcyl9PC9zcGFuPmA7CiAgICB9CiAgfSk7CgogIHJlbmRlclBsYXllcnMoKTsKICBpZih0eXBlb2YgWVQhPT0idW5kZWZpbmVkIiYmWVQuUGxheWVyKSBzZXRBY3RpdmUoYWN0aXZlSWR4KTsKfQoKZnVuY3Rpb24gcmVuZGVyUGxheWVycygpewogIGlmKHR5cGVvZiBZVD09PSJ1bmRlZmluZWQifHwhWVQuUGxheWVyKSByZXR1cm47CiAgdmlkZW9zLmZvckVhY2godj0+ewogICAgaWYocGxheWVyc1t2LnZpZGVvX2lkXSkgcmV0dXJuOwogICAgcGxheWVyc1t2LnZpZGVvX2lkXT1uZXcgWVQuUGxheWVyKGBwbC0ke3YudmlkZW9faWR9YCx7CiAgICAgIHZpZGVvSWQ6di52aWRlb19pZCwKICAgICAgcGxheWVyVmFyczp7YXV0b3BsYXk6MCxtdXRlOjEsY29udHJvbHM6MCxtb2Rlc3RicmFuZGluZzoxLHJlbDowfSwKICAgICAgZXZlbnRzOntvblN0YXRlQ2hhbmdlOihlKT0+eyBpZihlLmRhdGE9PT1ZVC5QbGF5ZXJTdGF0ZS5FTkRFRCkgYWR2YW5jZUFjdGl2ZSgpOyB9fQogICAgfSk7CiAgfSk7Cn0KCmZ1bmN0aW9uIHNldEFjdGl2ZShpZHgpewogIGlmKCF2aWRlb3MubGVuZ3RoKSByZXR1cm47CiAgYWN0aXZlSWR4PSgoaWR4JXZpZGVvcy5sZW5ndGgpK3ZpZGVvcy5sZW5ndGgpJXZpZGVvcy5sZW5ndGg7CiAgY29uc3QgdmlkPXZpZGVvc1thY3RpdmVJZHhdOwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5jYXJkIikuZm9yRWFjaChjPT5jLmNsYXNzTGlzdC5yZW1vdmUoInBsYXlpbmciKSk7CiAgZG9jdW1lbnQucXVlcnlTZWxlY3RvcihgW2RhdGEtdmlkPSIke3ZpZC52aWRlb19pZH0iXWApPy5jbGFzc0xpc3QuYWRkKCJwbGF5aW5nIik7CiAgT2JqZWN0LmVudHJpZXMocGxheWVycykuZm9yRWFjaCgoW2lkLHBdKT0+ewogICAgaWYoaWQ9PT12aWQudmlkZW9faWQpeyB0cnl7cC5sb2FkVmlkZW9CeUlkKHt2aWRlb0lkOmlkLHN0YXJ0U2Vjb25kczowfSk7cC5tdXRlKCk7fWNhdGNoKF8pe30gfQogICAgZWxzZSB7IHRyeXtwLnN0b3BWaWRlbygpO31jYXRjaChfKXt9IH0KICB9KTsKICBjbGVhclRpbWVvdXQoaG9sZFRpbWVyKTsKICBob2xkVGltZXI9c2V0VGltZW91dChhZHZhbmNlQWN0aXZlLEhPTERfU0VDUyoxMDAwKTsKfQpmdW5jdGlvbiBhZHZhbmNlQWN0aXZlKCl7IHNldEFjdGl2ZShhY3RpdmVJZHgrMSk7IH0KCi8vIOKUgOKUgCBDaGFydCDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKZnVuY3Rpb24gYnVpbGRDaGFydChkYXRhKXsKICBjb25zdCB3cmFwPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjaGFydC13cmFwIik7CiAgY29uc3QgY3Y9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNoYXJ0Iik7CiAgY3Yud2lkdGg9d3JhcC5jbGllbnRXaWR0aDsgY3YuaGVpZ2h0PXdyYXAuY2xpZW50SGVpZ2h0OwoKICBjb25zdCBwZWFrPWRhdGEudmFsdWVzLmxlbmd0aD9NYXRoLm1heCguLi5kYXRhLnZhbHVlcyk6MDsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgicGVhay12YWwiKS50ZXh0Q29udGVudD1mbXQocGVhaykrIiB2aWV3cyI7CgogIGlmKGNoYXJ0KSBjaGFydC5kZXN0cm95KCk7CiAgY2hhcnQ9bmV3IENoYXJ0KGN2LmdldENvbnRleHQoIjJkIiksewogICAgdHlwZToiYmFyIiwKICAgIGRhdGE6ewogICAgICBsYWJlbHM6ZGF0YS5sYWJlbHMsCiAgICAgIGRhdGFzZXRzOlt7CiAgICAgICAgZGF0YTpkYXRhLnZhbHVlcywKICAgICAgICBiYWNrZ3JvdW5kQ29sb3I6KGN0eCk9PmN0eC5yYXc9PT1wZWFrPyJyZ2JhKDI1NSwwLDAsMC45NSkiOiJyZ2JhKDI1NSwwLDAsMC40NSkiLAogICAgICAgIGJvcmRlcldpZHRoOjAsCiAgICAgICAgYm9yZGVyUmFkaXVzOjIsCiAgICAgICAgaG92ZXJCYWNrZ3JvdW5kQ29sb3I6InJnYmEoMjU1LDAsMCwwLjkpIiwKICAgICAgfV0KICAgIH0sCiAgICBvcHRpb25zOnsKICAgICAgcmVzcG9uc2l2ZTpmYWxzZSwKICAgICAgYW5pbWF0aW9uOntkdXJhdGlvbjo4MDAsZGVsYXk6KGMpPT5jLmRhdGFJbmRleCozfSwKICAgICAgcGx1Z2luczp7CiAgICAgICAgbGVnZW5kOntkaXNwbGF5OmZhbHNlfSwKICAgICAgICB0b29sdGlwOnsKICAgICAgICAgIGJhY2tncm91bmRDb2xvcjoicmdiYSgwLDAsMCwwLjg1KSIsCiAgICAgICAgICB0aXRsZUNvbG9yOiIjODg4Iixib2R5Q29sb3I6IiNmZmYiLHBhZGRpbmc6OCwKICAgICAgICAgIGNhbGxiYWNrczp7bGFiZWw6KGMpPT5gICR7Zm10KGMucmF3KX0gdmlld3NgfQogICAgICAgIH0KICAgICAgfSwKICAgICAgc2NhbGVzOnsKICAgICAgICB4Ont0aWNrczp7Y29sb3I6IiMzODM4MzgiLGZvbnQ6e3NpemU6OH0sbWF4Um90YXRpb246MCxtYXhUaWNrc0xpbWl0OjEzfSxncmlkOntjb2xvcjoicmdiYSgyNTUsMjU1LDI1NSwwLjAyKSJ9LGJvcmRlcjp7Y29sb3I6IiMxYTFhMWEifX0sCiAgICAgICAgeTp7dGlja3M6e2NvbG9yOiIjMzgzODM4Iixmb250OntzaXplOjh9LGNhbGxiYWNrOih2KT0+Zm10KHYpfSxncmlkOntjb2xvcjoicmdiYSgyNTUsMjU1LDI1NSwwLjAzKSJ9LGJvcmRlcjp7Y29sb3I6IiMxYTFhMWEifX0KICAgICAgfQogICAgfQogIH0pOwp9CgovLyDilIDilIAgQm9vdCDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKcmVmcmVzaCgpOwo8L3NjcmlwdD4KPC9ib2R5Pgo8L2h0bWw+Cg=="
DEMO_B64   = "PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImVuIj4KPGhlYWQ+CjxtZXRhIGNoYXJzZXQ9IlVURi04Ij4KPG1ldGEgbmFtZT0idmlld3BvcnQiIGNvbnRlbnQ9IndpZHRoPTEwODAiPgo8dGl0bGU+WW91VHViZSBTaG9ydHMg4oCUIERlbW88L3RpdGxlPgo8c3R5bGU+CiAgOnJvb3QgewogICAgLS1yZWQ6ICAgICAgI0ZGMDAwMDsKICAgIC0tcmVkLWRpbTogIHJnYmEoMjU1LDAsMCwwLjE4KTsKICAgIC0tcmVkLWdsb3c6IHJnYmEoMjU1LDAsMCwwLjMwKTsKICAgIC0tYmc6ICAgICAgICMwODA4MDg7CiAgICAtLXN1cmZhY2U6ICAjMGYwZjBmOwogICAgLS1ib3JkZXI6ICAgIzFlMWUxZTsKICAgIC0tdGV4dDogICAgICNmZmZmZmY7CiAgICAtLWRpbTogICAgICAjNjA2MDYwOwogICAgLS1kaW0yOiAgICAgIzNhM2EzYTsKICB9CgogICogeyBib3gtc2l6aW5nOiBib3JkZXItYm94OyBtYXJnaW46IDA7IHBhZGRpbmc6IDA7IH0KCiAgaHRtbCwgYm9keSB7CiAgICB3aWR0aDogMTA4MHB4OwogICAgaGVpZ2h0OiAxOTIwcHg7CiAgICBvdmVyZmxvdzogaGlkZGVuOwogICAgYmFja2dyb3VuZDogdmFyKC0tYmcpOwogICAgY29sb3I6IHZhcigtLXRleHQpOwogICAgZm9udC1mYW1pbHk6IC1hcHBsZS1zeXN0ZW0sIEJsaW5rTWFjU3lzdGVtRm9udCwgIkhlbHZldGljYSBOZXVlIiwgQXJpYWwsIHNhbnMtc2VyaWY7CiAgICAtd2Via2l0LWZvbnQtc21vb3RoaW5nOiBhbnRpYWxpYXNlZDsKICB9CgogIGJvZHkgeyBkaXNwbGF5OiBmbGV4OyBmbGV4LWRpcmVjdGlvbjogY29sdW1uOyB9CgogIC8qIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgAogICAgIEhFQURFUgogIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAqLwogICNoZWFkZXIgewogICAgZmxleDogMCAwIDE5MnB4OwogICAgZGlzcGxheTogZmxleDsKICAgIGZsZXgtZGlyZWN0aW9uOiBjb2x1bW47CiAgICBqdXN0aWZ5LWNvbnRlbnQ6IGNlbnRlcjsKICAgIHBhZGRpbmc6IDAgMzZweDsKICAgIGJhY2tncm91bmQ6IHZhcigtLXN1cmZhY2UpOwogICAgYm9yZGVyLWJvdHRvbTogMXB4IHNvbGlkIHZhcigtLWJvcmRlcik7CiAgICBnYXA6IDE4cHg7CiAgfQoKICAjaGVhZGVyLXRvcCB7CiAgICBkaXNwbGF5OiBmbGV4OwogICAgYWxpZ24taXRlbXM6IGNlbnRlcjsKICAgIGp1c3RpZnktY29udGVudDogc3BhY2UtYmV0d2VlbjsKICB9CgogICNjaGFubmVsLWlkIHsKICAgIGRpc3BsYXk6IGZsZXg7CiAgICBhbGlnbi1pdGVtczogY2VudGVyOwogICAgZ2FwOiAxMnB4OwogIH0KCiAgI2NoYW5uZWwtaWNvbiB7CiAgICB3aWR0aDogNDRweDsgaGVpZ2h0OiA0NHB4OwogICAgYmFja2dyb3VuZDogdmFyKC0tcmVkKTsKICAgIGJvcmRlci1yYWRpdXM6IDUwJTsKICAgIGRpc3BsYXk6IGZsZXg7IGFsaWduLWl0ZW1zOiBjZW50ZXI7IGp1c3RpZnktY29udGVudDogY2VudGVyOwogICAgZmxleC1zaHJpbms6IDA7CiAgfQogICNjaGFubmVsLWljb24gc3ZnIHsgd2lkdGg6IDIycHg7IGhlaWdodDogMjJweDsgZmlsbDogI2ZmZjsgfQoKICAjY2hhbm5lbC1uYW1lIHsKICAgIGZvbnQtc2l6ZTogMTVweDsKICAgIGZvbnQtd2VpZ2h0OiA2MDA7CiAgICBjb2xvcjogdmFyKC0tdGV4dCk7CiAgICBsZXR0ZXItc3BhY2luZzogLTAuMnB4OwogIH0KICAjY2hhbm5lbC1oYW5kbGUgewogICAgZm9udC1zaXplOiAxMnB4OwogICAgY29sb3I6IHZhcigtLWRpbSk7CiAgICBtYXJnaW4tdG9wOiAxcHg7CiAgfQoKICAjeXQtYmFkZ2UgewogICAgZGlzcGxheTogZmxleDsKICAgIGFsaWduLWl0ZW1zOiBjZW50ZXI7CiAgICBnYXA6IDdweDsKICAgIHBhZGRpbmc6IDZweCAxNHB4OwogICAgYm9yZGVyOiAxcHggc29saWQgdmFyKC0tYm9yZGVyKTsKICAgIGJvcmRlci1yYWRpdXM6IDZweDsKICB9CiAgI3l0LWJhZGdlIHN2ZyB7IHdpZHRoOiAxOHB4OyBoZWlnaHQ6IDEzcHg7IH0KICAjeXQtYmFkZ2Ugc3BhbiB7IGZvbnQtc2l6ZTogMTFweDsgY29sb3I6IHZhcigtLWRpbSk7IGxldHRlci1zcGFjaW5nOiAwLjVweDsgdGV4dC10cmFuc2Zvcm06IHVwcGVyY2FzZTsgfQoKICAvKiBTdGF0IHJvdyAqLwogICNzdGF0LXJvdyB7CiAgICBkaXNwbGF5OiBmbGV4OwogICAgYWxpZ24taXRlbXM6IHN0cmV0Y2g7CiAgICBnYXA6IDA7CiAgfQoKICAuc3RhdCB7CiAgICBkaXNwbGF5OiBmbGV4OwogICAgYWxpZ24taXRlbXM6IGNlbnRlcjsKICAgIGdhcDogMTRweDsKICAgIHBhZGRpbmctcmlnaHQ6IDM2cHg7CiAgICBtYXJnaW4tcmlnaHQ6IDM2cHg7CiAgICBib3JkZXItcmlnaHQ6IDFweCBzb2xpZCB2YXIoLS1ib3JkZXIpOwogIH0KICAuc3RhdDpsYXN0LWNoaWxkIHsgYm9yZGVyLXJpZ2h0OiBub25lOyBwYWRkaW5nLXJpZ2h0OiAwOyBtYXJnaW4tcmlnaHQ6IDA7IH0KCiAgLnN0YXQtaWNvbiB7CiAgICB3aWR0aDogNDBweDsgaGVpZ2h0OiA0MHB4OwogICAgYmFja2dyb3VuZDogdmFyKC0tcmVkLWRpbSk7CiAgICBib3JkZXItcmFkaXVzOiAxMHB4OwogICAgZGlzcGxheTogZmxleDsgYWxpZ24taXRlbXM6IGNlbnRlcjsganVzdGlmeS1jb250ZW50OiBjZW50ZXI7CiAgICBmbGV4LXNocmluazogMDsKICB9CiAgLnN0YXQtaWNvbiBzdmcgeyB3aWR0aDogMjBweDsgaGVpZ2h0OiAyMHB4OyBmaWxsOiB2YXIoLS1yZWQpOyB9CgogIC5zdGF0LWJvZHkgeyBkaXNwbGF5OiBmbGV4OyBmbGV4LWRpcmVjdGlvbjogY29sdW1uOyB9CgogIC5zdGF0LXZhbHVlIHsKICAgIGZvbnQtc2l6ZTogNDJweDsKICAgIGZvbnQtd2VpZ2h0OiA4MDA7CiAgICBsZXR0ZXItc3BhY2luZzogLTJweDsKICAgIGxpbmUtaGVpZ2h0OiAxOwogICAgZm9udC12YXJpYW50LW51bWVyaWM6IHRhYnVsYXItbnVtczsKICAgIGNvbG9yOiB2YXIoLS10ZXh0KTsKICB9CiAgLnN0YXQtdmFsdWUuYWNjZW50IHsgY29sb3I6IHZhcigtLXJlZCk7IH0KCiAgLnN0YXQtbWV0YSB7CiAgICBmb250LXNpemU6IDEwcHg7CiAgICBjb2xvcjogdmFyKC0tZGltKTsKICAgIHRleHQtdHJhbnNmb3JtOiB1cHBlcmNhc2U7CiAgICBsZXR0ZXItc3BhY2luZzogMS4ycHg7CiAgICBtYXJnaW4tdG9wOiA0cHg7CiAgfQoKICAvKiDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKICAgICBTRUNUSU9OIExBQkVMCiAg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAICovCiAgI3NlY3Rpb24tbGFiZWwgewogICAgZmxleDogMCAwIDM4cHg7CiAgICBkaXNwbGF5OiBmbGV4OwogICAgYWxpZ24taXRlbXM6IGNlbnRlcjsKICAgIGp1c3RpZnktY29udGVudDogc3BhY2UtYmV0d2VlbjsKICAgIHBhZGRpbmc6IDAgMzZweDsKICAgIGJhY2tncm91bmQ6IHZhcigtLWJnKTsKICAgIGJvcmRlci1ib3R0b206IDFweCBzb2xpZCB2YXIoLS1ib3JkZXIpOwogIH0KCiAgI3NlY3Rpb24tdGl0bGUgewogICAgZm9udC1zaXplOiAxMHB4OwogICAgZm9udC13ZWlnaHQ6IDcwMDsKICAgIGNvbG9yOiB2YXIoLS1kaW0pOwogICAgdGV4dC10cmFuc2Zvcm06IHVwcGVyY2FzZTsKICAgIGxldHRlci1zcGFjaW5nOiAycHg7CiAgfQoKICAjcmVmcmVzaC1iYWRnZSB7CiAgICBmb250LXNpemU6IDEwcHg7CiAgICBjb2xvcjogdmFyKC0tZGltMik7CiAgICBsZXR0ZXItc3BhY2luZzogMC41cHg7CiAgfQogICNyZWZyZXNoLWJhZGdlIHNwYW4geyBjb2xvcjogdmFyKC0tZGltKTsgfQoKICAvKiDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKICAgICBHUklECiAg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSAICovCiAgI2dyaWQgewogICAgZmxleDogMTsKICAgIGRpc3BsYXk6IGdyaWQ7CiAgICBncmlkLXRlbXBsYXRlLWNvbHVtbnM6IHJlcGVhdCgzLCAxZnIpOwogICAgZ3JpZC10ZW1wbGF0ZS1yb3dzOiByZXBlYXQoMywgMWZyKTsKICAgIGdhcDogMnB4OwogICAgYmFja2dyb3VuZDogIzAwMDsKICB9CgogIC5jYXJkIHsKICAgIHBvc2l0aW9uOiByZWxhdGl2ZTsKICAgIG92ZXJmbG93OiBoaWRkZW47CiAgICBiYWNrZ3JvdW5kOiAjMTExOwogICAgdHJhbnNpdGlvbjogYm94LXNoYWRvdyAwLjI1cyBlYXNlOwogIH0KCiAgLmNhcmQuYWN0aXZlIHsKICAgIGJveC1zaGFkb3c6IGluc2V0IDAgMCAwIDJweCB2YXIoLS1yZWQpOwogICAgei1pbmRleDogMjsKICB9CgogIC8qIElubmVyIHJlZCBib3JkZXIgZ2xvdyBmb3IgYWN0aXZlIGNhcmQgKi8KICAuY2FyZC5hY3RpdmU6OmJlZm9yZSB7CiAgICBjb250ZW50OiAiIjsKICAgIHBvc2l0aW9uOiBhYnNvbHV0ZTsKICAgIGluc2V0OiAwOwogICAgYm94LXNoYWRvdzogaW5zZXQgMCAwIDQwcHggcmdiYSgyNTUsMCwwLDAuMTgpOwogICAgei1pbmRleDogMzsKICAgIHBvaW50ZXItZXZlbnRzOiBub25lOwogIH0KCiAgLmNhcmQtYmcgewogICAgcG9zaXRpb246IGFic29sdXRlOwogICAgaW5zZXQ6IDA7CiAgICB3aWR0aDogMTAwJTsKICAgIGhlaWdodDogMTAwJTsKICB9CgogIC8qIE92ZXJsYXk6IGJvdHRvbSBncmFkaWVudCArIHN0YXRzICovCiAgLmNhcmQtb3ZlcmxheSB7CiAgICBwb3NpdGlvbjogYWJzb2x1dGU7CiAgICBib3R0b206IDA7IGxlZnQ6IDA7IHJpZ2h0OiAwOwogICAgcGFkZGluZzogMjhweCAxNHB4IDEycHg7CiAgICBiYWNrZ3JvdW5kOiBsaW5lYXItZ3JhZGllbnQodG8gdG9wLCByZ2JhKDAsMCwwLDAuOTIpIDAlLCByZ2JhKDAsMCwwLDAuNSkgNjAlLCB0cmFuc3BhcmVudCAxMDAlKTsKICAgIHotaW5kZXg6IDI7CiAgfQoKICAuY2FyZC1zdGF0cyB7CiAgICBkaXNwbGF5OiBmbGV4OwogICAgYWxpZ24taXRlbXM6IGNlbnRlcjsKICAgIGdhcDogMTRweDsKICB9CgogIC5jcyB7CiAgICBkaXNwbGF5OiBmbGV4OwogICAgYWxpZ24taXRlbXM6IGNlbnRlcjsKICAgIGdhcDogNXB4OwogICAgZm9udC1zaXplOiAxM3B4OwogICAgZm9udC13ZWlnaHQ6IDcwMDsKICAgIGNvbG9yOiAjZmZmOwogICAgbGV0dGVyLXNwYWNpbmc6IC0wLjNweDsKICB9CiAgLmNzIHN2ZyB7IHdpZHRoOiAxMnB4OyBoZWlnaHQ6IDEycHg7IGZpbGw6IHJnYmEoMjU1LDI1NSwyNTUsMC41NSk7IGZsZXgtc2hyaW5rOiAwOyB9CgogIC5jYXJkLXJhbmsgewogICAgcG9zaXRpb246IGFic29sdXRlOwogICAgdG9wOiAxMHB4OwogICAgbGVmdDogMTBweDsKICAgIHotaW5kZXg6IDM7CiAgICBmb250LXNpemU6IDExcHg7CiAgICBmb250LXdlaWdodDogODAwOwogICAgY29sb3I6IHJnYmEoMjU1LDI1NSwyNTUsMC45KTsKICAgIGJhY2tncm91bmQ6IHJnYmEoMCwwLDAsMC41NSk7CiAgICBib3JkZXI6IDFweCBzb2xpZCByZ2JhKDI1NSwyNTUsMjU1LDAuMTIpOwogICAgd2lkdGg6IDI0cHg7IGhlaWdodDogMjRweDsKICAgIGJvcmRlci1yYWRpdXM6IDUwJTsKICAgIGRpc3BsYXk6IGZsZXg7IGFsaWduLWl0ZW1zOiBjZW50ZXI7IGp1c3RpZnktY29udGVudDogY2VudGVyOwogICAgYmFja2Ryb3AtZmlsdGVyOiBibHVyKDRweCk7CiAgfQoKICAvKiBQbGF5aW5nIGluZGljYXRvciAqLwogIC5jYXJkLmFjdGl2ZSAuY2FyZC1wbGF5aW5nIHsKICAgIGRpc3BsYXk6IGZsZXg7CiAgfQogIC5jYXJkLXBsYXlpbmcgewogICAgZGlzcGxheTogbm9uZTsKICAgIHBvc2l0aW9uOiBhYnNvbHV0ZTsKICAgIHRvcDogMTBweDsgcmlnaHQ6IDEwcHg7CiAgICB6LWluZGV4OiAzOwogICAgYWxpZ24taXRlbXM6IGNlbnRlcjsKICAgIGdhcDogNXB4OwogICAgYmFja2dyb3VuZDogdmFyKC0tcmVkKTsKICAgIHBhZGRpbmc6IDNweCA5cHg7CiAgICBib3JkZXItcmFkaXVzOiA0cHg7CiAgICBmb250LXNpemU6IDlweDsKICAgIGZvbnQtd2VpZ2h0OiA4MDA7CiAgICBsZXR0ZXItc3BhY2luZzogMC44cHg7CiAgICBhbmltYXRpb246IGJsaW5rIDEuOHMgZWFzZS1pbi1vdXQgaW5maW5pdGU7CiAgfQogIC5jYXJkLXBsYXlpbmc6OmJlZm9yZSB7CiAgICBjb250ZW50OiAiIjsKICAgIHdpZHRoOiA1cHg7IGhlaWdodDogNXB4OwogICAgYm9yZGVyLXJhZGl1czogNTAlOwogICAgYmFja2dyb3VuZDogI2ZmZjsKICAgIGFuaW1hdGlvbjogYmxpbmsgMS44cyBlYXNlLWluLW91dCBpbmZpbml0ZTsKICB9CiAgQGtleWZyYW1lcyBibGluayB7IDAlLDEwMCV7b3BhY2l0eToxfSA1MCV7b3BhY2l0eTowLjQ1fSB9CgogIC8qIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgAogICAgIENIQVJUIFNFQ1RJT04KICDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAgKi8KICAjY2hhcnQtc2VjdGlvbiB7CiAgICBmbGV4OiAwIDAgMjY0cHg7CiAgICBiYWNrZ3JvdW5kOiB2YXIoLS1zdXJmYWNlKTsKICAgIGJvcmRlci10b3A6IDFweCBzb2xpZCB2YXIoLS1ib3JkZXIpOwogICAgcGFkZGluZzogMThweCAyOHB4IDE0cHg7CiAgICBkaXNwbGF5OiBmbGV4OwogICAgZmxleC1kaXJlY3Rpb246IGNvbHVtbjsKICAgIGdhcDogMTBweDsKICB9CgogICNjaGFydC1oZWFkZXIgewogICAgZGlzcGxheTogZmxleDsKICAgIGFsaWduLWl0ZW1zOiBjZW50ZXI7CiAgICBqdXN0aWZ5LWNvbnRlbnQ6IHNwYWNlLWJldHdlZW47CiAgfQoKICAjY2hhcnQtbGFiZWwgewogICAgZm9udC1zaXplOiAxMHB4OwogICAgZm9udC13ZWlnaHQ6IDcwMDsKICAgIGNvbG9yOiB2YXIoLS1kaW0pOwogICAgdGV4dC10cmFuc2Zvcm06IHVwcGVyY2FzZTsKICAgIGxldHRlci1zcGFjaW5nOiAycHg7CiAgfQoKICAjY2hhcnQtcGVhayB7CiAgICBmb250LXNpemU6IDEwcHg7CiAgICBjb2xvcjogdmFyKC0tZGltMik7CiAgfQogICNjaGFydC1wZWFrIHN0cm9uZyB7IGNvbG9yOiB2YXIoLS1kaW0pOyBmb250LXdlaWdodDogNjAwOyB9CgogICNjaGFydC13cmFwIHsKICAgIGZsZXg6IDE7CiAgICBwb3NpdGlvbjogcmVsYXRpdmU7CiAgfQoKICBjYW52YXMgeyBkaXNwbGF5OiBibG9jazsgfQo8L3N0eWxlPgo8L2hlYWQ+Cjxib2R5PgoKPCEtLSDilIDilIAgSGVhZGVyIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAtLT4KPGRpdiBpZD0iaGVhZGVyIj4KICA8ZGl2IGlkPSJoZWFkZXItdG9wIj4KICAgIDxkaXYgaWQ9ImNoYW5uZWwtaWQiPgogICAgICA8ZGl2IGlkPSJjaGFubmVsLWljb24iPgogICAgICAgIDwhLS0gWW91VHViZSBwbGF5IGljb24gLS0+CiAgICAgICAgPHN2ZyB2aWV3Qm94PSIwIDAgMjQgMjQiPjxwYXRoIGQ9Ik0xMCAxNS41bDYtMy41LTYtMy41djd6Ii8+PHBhdGggZD0iTTEyIDJDNi40OCAyIDIgNi40OCAyIDEyczQuNDggMTAgMTAgMTAgMTAtNC40OCAxMC0xMFMxNy41MiAyIDEyIDJ6bTAgMThjLTQuNDEgMC04LTMuNTktOC04czMuNTktOCA4LTggOCAzLjU5IDggOC0zLjU5IDgtOCA4eiIgb3BhY2l0eT0iLjAiLz48L3N2Zz4KICAgICAgPC9kaXY+CiAgICAgIDxkaXY+CiAgICAgICAgPGRpdiBpZD0iY2hhbm5lbC1uYW1lIj5Zb3VyIENoYW5uZWwgTmFtZTwvZGl2PgogICAgICAgIDxkaXYgaWQ9ImNoYW5uZWwtaGFuZGxlIj5AeW91cmNoYW5uZWwgwrcgU2hvcnRzPC9kaXY+CiAgICAgIDwvZGl2PgogICAgPC9kaXY+CiAgICA8ZGl2IGlkPSJ5dC1iYWRnZSI+CiAgICAgIDwhLS0gWW91VHViZSB3b3JkbWFyayAtLT4KICAgICAgPHN2ZyB2aWV3Qm94PSIwIDAgOTAgMjAiIGZpbGw9Im5vbmUiPgogICAgICAgIDxwYXRoIGQ9Ik0yNy45IDMuNWEzLjUgMy41IDAgMCAwLTIuNS0yLjVDMjMuMi41IDE0LjUuNSAxNC41LjVTNS44LjUgMy42IDFjLTEuMi4zLTIuMiAxLjMtMi41IDIuNUMuNiA1LjcuNiAxMCAuNiAxMHMwIDQuMy41IDYuNWMuMyAxLjIgMS4zIDIuMiAyLjUgMi41IDIuMi42IDEwLjkuNiAxMC45LjZzOC43IDAgMTAuOS0uNmMxLjItLjMgMi4yLTEuMyAyLjUtMi41LjUtMi4yLjUtNi41LjUtNi41czAtNC4zLS41LTYuNXoiIGZpbGw9IiNGRjAwMDAiLz4KICAgICAgICA8cGF0aCBkPSJNMTEuOCAxNGw3LjItNC03LjItNHY4eiIgZmlsbD0iI2ZmZiIvPgogICAgICAgIDxwYXRoIGQ9Ik0zNi41IDEzLjdMMzMuMyAzaDIuMmwxLjMgNS4xYy4zIDEuMy42IDIuNC43IDMuM2guMWMuMS0uNi40LTEuNy43LTMuM0wzOS42IDNoMi4ybC0zLjMgMTAuN3Y1aC0ydi01ek00NyAxOC44Yy0uNiAwLTEuMi0uMi0xLjYtLjVzLS44LS44LTEtMS40aC0uMWwtLjIgMS43SDQyLjVWM2gydjUuOWguMWMuMi0uNi41LTEgLjktMS40LjQtLjQuOS0uNiAxLjUtLjYgMSAwIDEuOC40IDIuMyAxLjIuNS44LjggMiAuOCAzLjZ2LjhjMCAxLjUtLjMgMi43LS44IDMuNS0uNS44LTEuMyAxLjItMi4zIDEuOHptLS42LTJjLjUgMCAuOS0uMyAxLjEtLjguMi0uNS40LTEuMy40LTIuM3YtLjhjMC0xLS4xLTEuOC0uNC0yLjMtLjItLjUtLjYtLjgtMS4xLS44LS40IDAtLjcuMS0uOS40LS4yLjMtLjQuNi0uNSAxdjQuMmMuMS40LjMuNy41IDEgLjIuMi41LjQuOS40em05LjYgMmMtMS4xIDAtMi0uNC0yLjYtMS4yLS42LS44LS45LTItLjktMy41di0uOWMwLTEuNS4zLTIuNy45LTMuNS42LS44IDEuNS0xLjIgMi42LTEuMnMyIC40IDIuNiAxLjJjLjYuOC45IDIgLjkgMy41di45YzAgMS41LS4zIDIuNy0uOSAzLjUtLjYuOC0xLjUgMS4yLTIuNiAxLjJ6bTAtMS44Yy41IDAgLjktLjMgMS4xLS44LjItLjUuMy0xLjMuMy0yLjN2LS45YzAtMS0uMS0xLjgtLjMtMi4zLS4yLS41LS42LS44LTEuMS0uOC0uNSAwLS45LjMtMS4xLjgtLjIuNS0uMyAxLjMtLjMgMi4zdi45YzAgMSAuMSAxLjguMyAyLjMuMi41LjYuOCAxLjEuOHptOC4yIDEuOGMtLjYgMC0xLjEtLjItMS40LS41LS4zLS4zLS41LS44LS42LTEuNGgtLjF2MS43aC0xLjdWNy41aDJ2Ni43YzAgLjcuMSAxLjMuMyAxLjYuMi4zLjUuNS45LjUuMiAwIC40IDAgLjYtLjFsLjIgMS44Yy0uNC4xLS44LjItMS4yLjJ6bTUuNyAwYy0xLjEgMC0yLS40LTIuNi0xLjItLjYtLjgtLjktMi0uOS0zLjV2LS45YzAtMS41LjMtMi43LjktMy41LjYtLjggMS41LTEuMiAyLjYtMS4yLjkgMCAxLjYuMyAyLjEuOC41LjUuOCAxLjMuOSAyLjNoLTEuOWMtLjEtLjUtLjMtLjktLjUtMS4xLS4yLS4yLS41LS4zLS44LS4zLS41IDAtLjkuMy0xLjEuOC0uMi41LS40IDEuMy0uNCAyLjN2LjljMCAxIC4xIDEuOC40IDIuMy4yLjUuNi44IDEuMS44LjMgMCAuNi0uMS44LS40LjItLjIuNC0uNi41LTEuMWgxLjljLS4xIDEtLjQgMS43LS45IDIuMi0uNS42LTEuMi44LTIuMS44eiIgZmlsbD0iI2ZmZiIvPgogICAgICA8L3N2Zz4KICAgICAgPHNwYW4+TGl2ZTwvc3Bhbj4KICAgIDwvZGl2PgogIDwvZGl2PgoKICA8ZGl2IGlkPSJzdGF0LXJvdyI+CiAgICA8ZGl2IGNsYXNzPSJzdGF0Ij4KICAgICAgPGRpdiBjbGFzcz0ic3RhdC1pY29uIj4KICAgICAgICA8c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZD0iTTEyIDQuNUM3IDQuNSAyLjczIDcuNjEgMSAxMmMxLjczIDQuMzkgNiA3LjUgMTEgNy41czkuMjctMy4xMSAxMS03LjVjLTEuNzMtNC4zOS02LTcuNS0xMS03LjV6bTAgMTIuNWE1IDUgMCAxIDEgMC0xMCA1IDUgMCAwIDEgMCAxMHptMC04YTMgMyAwIDEgMCAwIDYgMyAzIDAgMCAwIDAtNnoiLz48L3N2Zz4KICAgICAgPC9kaXY+CiAgICAgIDxkaXYgY2xhc3M9InN0YXQtYm9keSI+CiAgICAgICAgPGRpdiBjbGFzcz0ic3RhdC12YWx1ZSBhY2NlbnQiIGlkPSJzdGF0LXZpZXdzIj4wPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0ic3RhdC1tZXRhIj5WaWV3cyDCtyA3IGRheXM8L2Rpdj4KICAgICAgPC9kaXY+CiAgICA8L2Rpdj4KICAgIDxkaXYgY2xhc3M9InN0YXQiPgogICAgICA8ZGl2IGNsYXNzPSJzdGF0LWljb24iPgogICAgICAgIDxzdmcgdmlld0JveD0iMCAwIDI0IDI0Ij48cGF0aCBkPSJNMSAyMWg0VjlIMXYxMnptMjItMTFjMC0xLjEtLjktMi0yLTJoLTYuMzFsLjk1LTQuNTcuMDMtLjMyYzAtLjQxLS4xNy0uNzktLjQ0LTEuMDZMMTQuMTcgMSA3LjU5IDcuNTlDNy4yMiA3Ljk1IDcgOC40NSA3IDl2MTBjMCAxLjEuOSAyIDIgMmg5Yy44MyAwIDEuNTQtLjUgMS44NC0xLjIybDMuMDItNy4wNWMuMDktLjIzLjE0LS40Ny4xNC0uNzN2LTJ6Ii8+PC9zdmc+CiAgICAgIDwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGF0LWJvZHkiPgogICAgICAgIDxkaXYgY2xhc3M9InN0YXQtdmFsdWUiIGlkPSJzdGF0LWxpa2VzIj4wPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0ic3RhdC1tZXRhIj5MaWtlcyDCtyA3IGRheXM8L2Rpdj4KICAgICAgPC9kaXY+CiAgICA8L2Rpdj4KICAgIDxkaXYgY2xhc3M9InN0YXQiPgogICAgICA8ZGl2IGNsYXNzPSJzdGF0LWljb24iPgogICAgICAgIDxzdmcgdmlld0JveD0iMCAwIDI0IDI0Ij48cGF0aCBkPSJNMjEgNmgtMnY5SDZ2MmMwIC41NS40NSAxIDEgMWgxMWw0IDRWN2MwLS41NS0uNDUtMS0xLTF6bS00IDZWM2MwLS41NS0uNDUtMS0xLTFIM2MtLjU1IDAtMSAuNDUtMSAxdjE0bDQtNGgxMGMuNTUgMCAxLS40NSAxLTF6Ii8+PC9zdmc+CiAgICAgIDwvZGl2PgogICAgICA8ZGl2IGNsYXNzPSJzdGF0LWJvZHkiPgogICAgICAgIDxkaXYgY2xhc3M9InN0YXQtdmFsdWUiIGlkPSJzdGF0LWNvbW1lbnRzIj4wPC9kaXY+CiAgICAgICAgPGRpdiBjbGFzcz0ic3RhdC1tZXRhIj5Db21tZW50cyDCtyA3IGRheXM8L2Rpdj4KICAgICAgPC9kaXY+CiAgICA8L2Rpdj4KICA8L2Rpdj4KPC9kaXY+Cgo8IS0tIOKUgOKUgCBTZWN0aW9uIGxhYmVsIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAtLT4KPGRpdiBpZD0ic2VjdGlvbi1sYWJlbCI+CiAgPHNwYW4gaWQ9InNlY3Rpb24tdGl0bGUiPlRvcCBTaG9ydHMgwrcgTGFzdCA3IERheXM8L3NwYW4+CiAgPHNwYW4gaWQ9InJlZnJlc2gtYmFkZ2UiPk5leHQgcmVmcmVzaCBpbiA8c3BhbiBpZD0iY291bnRkb3duIj4xNDo1Mjwvc3Bhbj48L3NwYW4+CjwvZGl2PgoKPCEtLSDilIDilIAgM8OXMyBHcmlkIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgCAtLT4KPGRpdiBpZD0iZ3JpZCI+PC9kaXY+Cgo8IS0tIOKUgOKUgCBDaGFydCDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAgLS0+CjxkaXYgaWQ9ImNoYXJ0LXNlY3Rpb24iPgogIDxkaXYgaWQ9ImNoYXJ0LWhlYWRlciI+CiAgICA8c3BhbiBpZD0iY2hhcnQtbGFiZWwiPlZpZXdzIMK3IExhc3QgMjQgSG91cnM8L3NwYW4+CiAgICA8c3BhbiBpZD0iY2hhcnQtcGVhayI+UGVhayA8c3Ryb25nIGlkPSJwZWFrLXZhbCI+4oCUPC9zdHJvbmc+PC9zcGFuPgogIDwvZGl2PgogIDxkaXYgaWQ9ImNoYXJ0LXdyYXAiPgogICAgPGNhbnZhcyBpZD0iY2hhcnQiPjwvY2FudmFzPgogIDwvZGl2Pgo8L2Rpdj4KCjxzY3JpcHQgc3JjPSJodHRwczovL2Nkbi5qc2RlbGl2ci5uZXQvbnBtL2NoYXJ0LmpzQDQuNC4yL2Rpc3QvY2hhcnQudW1kLm1pbi5qcyI+PC9zY3JpcHQ+CjxzY3JpcHQ+Ci8vIOKUgOKUgCBNb2NrIGRhdGEg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmNvbnN0IE1FVFJJQ1MgPSB7IHZpZXdzOiAxMzgyMDAwMCwgbGlrZXM6IDk0NzAwMCwgY29tbWVudHM6IDM0MjAwIH07Cgpjb25zdCBWSURFT1MgPSBbCiAgeyBpZDoxLCB2OjM4NDAwMDAsIGw6Mjg3MDAwLCBjOjg0MjAsICBnOlsiIzBkMmI0YSIsIiMxYTVjOGEiXSwgbGFiZWw6Ik9jZWFuIiB9LAogIHsgaWQ6MiwgdjoyOTEwMDAwLCBsOjE5ODAwMCwgYzo2MjMwLCAgZzpbIiMzYTBhMGEiLCIjOGIxYTFhIl0sIGxhYmVsOiJGaXJlIiAgfSwKICB7IGlkOjMsIHY6MjM0MDAwMCwgbDoxNTYwMDAsIGM6NDg5MCwgIGc6WyIjMGEyYjE0IiwiIzFhNmIzNCJdLCBsYWJlbDoiRm9yZXN0In0sCiAgeyBpZDo0LCB2OjE4NzAwMDAsIGw6MTQzMDAwLCBjOjkxMDAsICBnOlsiIzFhMGEzYSIsIiM0YTFhOGEiXSwgbGFiZWw6Ik5pZ2h0IiB9LAogIHsgaWQ6NSwgdjoxNTYwMDAwLCBsOjExMjAwMCwgYzo1NjcwLCAgZzpbIiMwNDIwMjAiLCIjMGE1NTU1Il0sIGxhYmVsOiJUZWFsIiAgfSwKICB7IGlkOjYsIHY6MTIzMDAwMCwgbDo5ODcwMCwgIGM6MzIxMCwgIGc6WyIjMmExZTAwIiwiIzdhNWEwMCJdLCBsYWJlbDoiR29sZCIgIH0sCiAgeyBpZDo3LCB2Ojk4MDAwMCwgIGw6NzY1MDAsICBjOjQzMjAsICBnOlsiIzA4MGQyMCIsIiMxYTJkNjAiXSwgbGFiZWw6IldpbnRlciJ9LAogIHsgaWQ6OCwgdjo3NjAwMDAsICBsOjYxMjAwLCAgYzoyOTgwLCAgZzpbIiMyMDA4MDgiLCIjNjAxYTFhIl0sIGxhYmVsOiJDcmltc29uIn0sCiAgeyBpZDo5LCB2OjU0MDAwMCwgIGw6NDg5MDAsICBjOjE4NzAsICBnOlsiIzEwMTAxMCIsIiMyYTJhMmEiXSwgbGFiZWw6Ik1vbm8iICB9LApdOwoKZnVuY3Rpb24gZ2F1c3MoeCxtdSxzaWcsYW1wKXsgcmV0dXJuIGFtcCpNYXRoLmV4cCgtMC41Kk1hdGgucG93KCh4LW11KS9zaWcsMikpOyB9CmZ1bmN0aW9uIGJ1aWxkQ2hhcnQoKXsKICBjb25zdCBsYmw9W10sIHZhbD1bXTsKICBmb3IobGV0IGg9MDtoPDI0O2grKykgZm9yKGxldCBxPTA7cTw0O3ErKyl7CiAgICBjb25zdCB0PWgrcSowLjI1OwogICAgY29uc3Qgdj1NYXRoLnJvdW5kKAogICAgICBnYXVzcyh0LDcsMS41LDE0MDAwKStnYXVzcyh0LDEyLDIsMjAwMDApKwogICAgICBnYXVzcyh0LDE5LDIuNSwyODAwMCkrKE1hdGgucmFuZG9tKCktLjUpKjE2MDArNjAwCiAgICApOwogICAgbGJsLnB1c2goYCR7U3RyaW5nKGgpLnBhZFN0YXJ0KDIsIjAiKX06JHtTdHJpbmcocSoxNSkucGFkU3RhcnQoMiwiMCIpfWApOwogICAgdmFsLnB1c2goTWF0aC5tYXgoMTgwLHYpKTsKICB9CiAgcmV0dXJuIHtsYmwsdmFsfTsKfQoKLy8g4pSA4pSAIEZvcm1hdHRpbmcg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmZ1bmN0aW9uIGZtdChuKXsKICBpZihuPj0xZTYpIHJldHVybihuLzFlNikudG9GaXhlZChuPj0xMGU2PzE6MikrIk0iOwogIGlmKG4+PTFlMykgcmV0dXJuKG4vMWUzKS50b0ZpeGVkKG4+PTEwMGUzPzA6MSkrIksiOwogIHJldHVybiBuLnRvTG9jYWxlU3RyaW5nKCk7Cn0KCi8vIOKUgOKUgCBDb3VudC11cCDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKZnVuY3Rpb24gY291bnRVcChlbCx0YXJnZXQpewogIGNvbnN0IHN0ZXBzPTgwLGR1cj0yODAwOwogIGxldCBzdGVwPTA7CiAgY29uc3QgdD1zZXRJbnRlcnZhbCgoKT0+ewogICAgc3RlcCsrOwogICAgY29uc3QgcD1zdGVwL3N0ZXBzOwogICAgY29uc3QgZWFzZT1wPDAuNT8yKnAqcDotMSsoNC0yKnApKnA7CiAgICBlbC50ZXh0Q29udGVudD1mbXQoTWF0aC5yb3VuZCh0YXJnZXQqZWFzZSkpOwogICAgaWYoc3RlcD49c3RlcHMpe2NsZWFySW50ZXJ2YWwodCk7ZWwudGV4dENvbnRlbnQ9Zm10KHRhcmdldCk7fQogIH0sZHVyL3N0ZXBzKTsKfQoKLy8g4pSA4pSAIFNWRyBpY29ucyDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKY29uc3QgRVlFICA9IGA8c3ZnIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZD0iTTEyIDQuNUM3IDQuNSAyLjczIDcuNjEgMSAxMmMxLjczIDQuMzkgNiA3LjUgMTEgNy41czkuMjctMy4xMSAxMS03LjVjLTEuNzMtNC4zOS02LTcuNS0xMS03LjV6bTAgMTIuNWE1IDUgMCAxIDEgMC0xMCA1IDUgMCAwIDEgMCAxMHptMC04YTMgMyAwIDEgMCAwIDYgMyAzIDAgMCAwIDAtNnoiLz48L3N2Zz5gOwpjb25zdCBMSUtFID0gYDxzdmcgdmlld0JveD0iMCAwIDI0IDI0Ij48cGF0aCBkPSJNMSAyMWg0VjlIMXYxMnptMjItMTFjMC0xLjEtLjktMi0yLTJoLTYuMzFsLjk1LTQuNTcuMDMtLjMyYzAtLjQxLS4xNy0uNzktLjQ0LTEuMDZMMTQuMTcgMSA3LjU5IDcuNTlDNy4yMiA3Ljk1IDcgOXYxMGMwIDEuMS45IDIgMiAyaDljLjgzIDAgMS41NC0uNSAxLjg0LTEuMjJsMy4wMi03LjA1Yy4wOS0uMjMuMTQtLjQ3LjE0LS43M3YtMnoiLz48L3N2Zz5gOwoKLy8g4pSA4pSAIEdyaWQg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACmZ1bmN0aW9uIGJ1aWxkR3JpZCgpewogIGNvbnN0IGc9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImdyaWQiKTsKICBWSURFT1MuZm9yRWFjaCgodixpKT0+ewogICAgY29uc3QgY2FyZD1kb2N1bWVudC5jcmVhdGVFbGVtZW50KCJkaXYiKTsKICAgIGNhcmQuY2xhc3NOYW1lPSJjYXJkIisoaT09PTA/IiBhY3RpdmUiOiIiKTsKICAgIGNhcmQuZGF0YXNldC5pPWk7CgogICAgLy8gZ3JhZGllbnQgY2FudmFzIGFzIGJhY2tncm91bmQKICAgIGNvbnN0IGN2PWRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoImNhbnZhcyIpOwogICAgY3YuY2xhc3NOYW1lPSJjYXJkLWJnIjsKICAgIGNhcmQuYXBwZW5kQ2hpbGQoY3YpOwoKICAgIGNvbnN0IG92ZXJsYXk9ZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgiZGl2Iik7CiAgICBvdmVybGF5LmNsYXNzTmFtZT0iY2FyZC1vdmVybGF5IjsKICAgIG92ZXJsYXkuaW5uZXJIVE1MPWA8ZGl2IGNsYXNzPSJjYXJkLXN0YXRzIj4KICAgICAgPHNwYW4gY2xhc3M9ImNzIj4ke0VZRX0ke2ZtdCh2LnYpfTwvc3Bhbj4KICAgICAgPHNwYW4gY2xhc3M9ImNzIj4ke0xJS0V9JHtmbXQodi5sKX08L3NwYW4+CiAgICA8L2Rpdj5gOwoKICAgIGNvbnN0IHJhbms9ZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgiZGl2Iik7CiAgICByYW5rLmNsYXNzTmFtZT0iY2FyZC1yYW5rIjsKICAgIHJhbmsudGV4dENvbnRlbnQ9aSsxOwoKICAgIGNvbnN0IHBsYXlpbmc9ZG9jdW1lbnQuY3JlYXRlRWxlbWVudCgiZGl2Iik7CiAgICBwbGF5aW5nLmNsYXNzTmFtZT0iY2FyZC1wbGF5aW5nIjsKICAgIHBsYXlpbmcudGV4dENvbnRlbnQ9IkxJVkUiOwoKICAgIGNhcmQuYXBwZW5kQ2hpbGQob3ZlcmxheSk7CiAgICBjYXJkLmFwcGVuZENoaWxkKHJhbmspOwogICAgY2FyZC5hcHBlbmRDaGlsZChwbGF5aW5nKTsKICAgIGcuYXBwZW5kQ2hpbGQoY2FyZCk7CgogICAgLy8gRHJhdyBncmFkaWVudCBhZnRlciBsYXlvdXQKICAgIHJlcXVlc3RBbmltYXRpb25GcmFtZSgoKT0+ewogICAgICBjb25zdCByPWNhcmQuZ2V0Qm91bmRpbmdDbGllbnRSZWN0KCk7CiAgICAgIGNvbnN0IHc9ci53aWR0aHx8MzYwLCBoPXIuaGVpZ2h0fHw0ODA7CiAgICAgIGN2LndpZHRoPXc7IGN2LmhlaWdodD1oOwogICAgICBjb25zdCBjdHg9Y3YuZ2V0Q29udGV4dCgiMmQiKTsKICAgICAgY29uc3QgZ3I9Y3R4LmNyZWF0ZUxpbmVhckdyYWRpZW50KDAsMCx3KjAuNixoKTsKICAgICAgZ3IuYWRkQ29sb3JTdG9wKDAsdi5nWzFdKTsKICAgICAgZ3IuYWRkQ29sb3JTdG9wKDEsdi5nWzBdKTsKICAgICAgY3R4LmZpbGxTdHlsZT1ncjsKICAgICAgY3R4LmZpbGxSZWN0KDAsMCx3LGgpOwogICAgICAvLyBTdWJ0bGUgY2VudGVyIGxhYmVsCiAgICAgIGN0eC5maWxsU3R5bGU9InJnYmEoMjU1LDI1NSwyNTUsMC4wNikiOwogICAgICBjdHguZm9udD1gOTAwICR7TWF0aC5yb3VuZCh3KjAuMTIpfXB4IC1hcHBsZS1zeXN0ZW0sc2Fucy1zZXJpZmA7CiAgICAgIGN0eC50ZXh0QWxpZ249ImNlbnRlciI7CiAgICAgIGN0eC50ZXh0QmFzZWxpbmU9Im1pZGRsZSI7CiAgICAgIGN0eC5maWxsVGV4dCh2LmxhYmVsLHcvMixoLzIpOwogICAgfSk7CgogICAgY2FyZC5hZGRFdmVudExpc3RlbmVyKCJjbGljayIsKCk9PnNldEFjdGl2ZShpKSk7CiAgfSk7Cn0KCmxldCBhY3RpdmVJZHg9MCwgaG9sZFRpbWVyPW51bGw7CmZ1bmN0aW9uIHNldEFjdGl2ZShpZHgpewogIGFjdGl2ZUlkeD0oKGlkeCU5KSs5KSU5OwogIGRvY3VtZW50LnF1ZXJ5U2VsZWN0b3JBbGwoIi5jYXJkIikuZm9yRWFjaCgoYyxpKT0+Yy5jbGFzc0xpc3QudG9nZ2xlKCJhY3RpdmUiLGk9PT1hY3RpdmVJZHgpKTsKICBjbGVhclRpbWVvdXQoaG9sZFRpbWVyKTsKICBob2xkVGltZXI9c2V0VGltZW91dCgoKT0+c2V0QWN0aXZlKGFjdGl2ZUlkeCsxKSw0MDAwKTsKfQoKLy8g4pSA4pSAIENoYXJ0IOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgApmdW5jdGlvbiBidWlsZENoYXJ0Vml6KCl7CiAgY29uc3Qge2xibCx2YWx9PWJ1aWxkQ2hhcnQoKTsKICBjb25zdCBwZWFrPU1hdGgubWF4KC4uLnZhbCk7CiAgZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoInBlYWstdmFsIikudGV4dENvbnRlbnQ9Zm10KHBlYWspKyIgdmlld3MiOwoKICBjb25zdCB3cmFwPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJjaGFydC13cmFwIik7CiAgY29uc3QgY3Y9ZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoImNoYXJ0Iik7CiAgY3Yud2lkdGg9d3JhcC5jbGllbnRXaWR0aHx8MTAyNDsKICBjdi5oZWlnaHQ9d3JhcC5jbGllbnRIZWlnaHR8fDIwMDsKCiAgbmV3IENoYXJ0KGN2LmdldENvbnRleHQoIjJkIiksewogICAgdHlwZToiYmFyIiwKICAgIGRhdGE6ewogICAgICBsYWJlbHM6bGJsLAogICAgICBkYXRhc2V0czpbewogICAgICAgIGRhdGE6dmFsLAogICAgICAgIGJhY2tncm91bmRDb2xvcjooY3R4KT0+ewogICAgICAgICAgY29uc3QgbWF4PWN0eC5jaGFydC5kYXRhLmRhdGFzZXRzWzBdLmRhdGEucmVkdWNlKChhLGIpPT5NYXRoLm1heChhLGIpLDApOwogICAgICAgICAgcmV0dXJuIGN0eC5yYXc9PT1tYXg/InJnYmEoMjU1LDAsMCwwLjk1KSI6InJnYmEoMjU1LDAsMCwwLjQ1KSI7CiAgICAgICAgfSwKICAgICAgICBib3JkZXJXaWR0aDowLAogICAgICAgIGJvcmRlclJhZGl1czoyLAogICAgICAgIGhvdmVyQmFja2dyb3VuZENvbG9yOiJyZ2JhKDI1NSwwLDAsMC45KSIsCiAgICAgIH1dCiAgICB9LAogICAgb3B0aW9uczp7CiAgICAgIHJlc3BvbnNpdmU6ZmFsc2UsCiAgICAgIGFuaW1hdGlvbjp7ZHVyYXRpb246OTAwLGRlbGF5OihjKT0+Yy5kYXRhSW5kZXgqM30sCiAgICAgIHBsdWdpbnM6ewogICAgICAgIGxlZ2VuZDp7ZGlzcGxheTpmYWxzZX0sCiAgICAgICAgdG9vbHRpcDp7CiAgICAgICAgICBiYWNrZ3JvdW5kQ29sb3I6InJnYmEoMCwwLDAsMC44NSkiLAogICAgICAgICAgdGl0bGVDb2xvcjoiIzg4OCIsCiAgICAgICAgICBib2R5Q29sb3I6IiNmZmYiLAogICAgICAgICAgcGFkZGluZzo4LAogICAgICAgICAgY2FsbGJhY2tzOntsYWJlbDooYyk9PmAgJHtmbXQoYy5yYXcpfSB2aWV3c2B9CiAgICAgICAgfQogICAgICB9LAogICAgICBzY2FsZXM6ewogICAgICAgIHg6ewogICAgICAgICAgdGlja3M6e2NvbG9yOiIjMzgzODM4Iixmb250OntzaXplOjh9LG1heFJvdGF0aW9uOjAsbWF4VGlja3NMaW1pdDoxM30sCiAgICAgICAgICBncmlkOntjb2xvcjoicmdiYSgyNTUsMjU1LDI1NSwwLjAyKSJ9LAogICAgICAgICAgYm9yZGVyOntjb2xvcjoiIzFhMWExYSJ9LAogICAgICAgIH0sCiAgICAgICAgeTp7CiAgICAgICAgICB0aWNrczp7Y29sb3I6IiMzODM4MzgiLGZvbnQ6e3NpemU6OH0sY2FsbGJhY2s6KHYpPT5mbXQodil9LAogICAgICAgICAgZ3JpZDp7Y29sb3I6InJnYmEoMjU1LDI1NSwyNTUsMC4wMykifSwKICAgICAgICAgIGJvcmRlcjp7Y29sb3I6IiMxYTFhMWEifSwKICAgICAgICB9CiAgICAgIH0KICAgIH0KICB9KTsKfQoKLy8g4pSA4pSAIENvdW50ZG93biDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKbGV0IHNlY3M9MTQqNjArNTI7CnNldEludGVydmFsKCgpPT57CiAgc2Vjcz1NYXRoLm1heCgwLHNlY3MtMSk7CiAgY29uc3QgbT1TdHJpbmcoTWF0aC5mbG9vcihzZWNzLzYwKSkucGFkU3RhcnQoMiwiMCIpOwogIGNvbnN0IHM9U3RyaW5nKHNlY3MlNjApLnBhZFN0YXJ0KDIsIjAiKTsKICBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgiY291bnRkb3duIikudGV4dENvbnRlbnQ9YCR7bX06JHtzfWA7Cn0sMTAwMCk7CgovLyDilIDilIAgQm9vdCDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKYnVpbGRHcmlkKCk7CmJ1aWxkQ2hhcnRWaXooKTsKY291bnRVcChkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3RhdC12aWV3cyIpLCAgICBNRVRSSUNTLnZpZXdzKTsKY291bnRVcChkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3RhdC1saWtlcyIpLCAgICBNRVRSSUNTLmxpa2VzKTsKY291bnRVcChkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgic3RhdC1jb21tZW50cyIpLCBNRVRSSUNTLmNvbW1lbnRzKTsKc2V0QWN0aXZlKDApOwo8L3NjcmlwdD4KPC9ib2R5Pgo8L2h0bWw+Cg=="

BIN_FILES = {
    "frontend/youtube-shorts/index.html": INDEX_B64,
    "demo.html": DEMO_B64,
}

# ── Write all files ───────────────────────────────────────────────────────────

for rel, content in TEXT_FILES.items():
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    print(f"  wrote {rel}")

for rel, b64 in BIN_FILES.items():
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(b64))
    print(f"  wrote {rel} (binary)")

# ── Git commit + push ─────────────────────────────────────────────────────────

os.chdir(ROOT)
for cmd in [
    ["git", "add", "-A"],
    ["git", "commit", "-m", "Initial commit: YouTube Shorts live dashboard"],
    ["git", "push", "-u", "origin", "main"],
]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout, end="")
    if r.returncode != 0:
        print(f"[warn] {cmd}: {r.stderr.strip()}", file=sys.stderr)

print(f"""
==> Done!
    cd {ROOT}
    cp .env.example .env && nano .env
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python run.py
""")
