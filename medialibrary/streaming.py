"""What's streaming where: subscription availability from TMDB (data by JustWatch).

Availability only - never used as a measure of a film's standing. The full
catalog of each service in STREAMING_SERVICES is pulled from TMDB's discover
endpoint and refreshed daily.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, select

from medialibrary.config import settings
from medialibrary.database import AppMeta, StreamingTitle

REGION = "US"
# TMDB provider names exactly as TMDB lists them for the US.
STREAMING_SERVICES = ["Criterion Channel"]
SEARCH_URLS = {
    "Criterion Channel": "https://www.criterionchannel.com/search?q={q}",
}
REFRESH_EVERY = timedelta(hours=24)
MAX_PAGES = 500   # TMDB's cap on discover pages


async def _provider_ids(client: httpx.AsyncClient) -> dict[str, int]:
    r = await client.get(f"{settings.tmdb_base_url}/watch/providers/movie",
                         params={"api_key": settings.tmdb_api_key, "watch_region": REGION})
    r.raise_for_status()
    by_name = {p["provider_name"]: p["provider_id"] for p in r.json().get("results", [])}
    return {name: by_name[name] for name in STREAMING_SERVICES if name in by_name}


async def _catalog(client: httpx.AsyncClient, provider_id: int) -> list[dict]:
    params = {
        "api_key": settings.tmdb_api_key, "watch_region": REGION,
        "with_watch_providers": str(provider_id), "with_watch_monetization_types": "flatrate",
        "sort_by": "popularity.desc", "include_adult": "false",
    }

    async def page(n: int) -> dict:
        for attempt in range(4):
            r = await client.get(f"{settings.tmdb_base_url}/discover/movie", params={**params, "page": n})
            if r.status_code == 429:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"TMDB kept rate-limiting page {n}")

    first = await page(1)
    pages = min(first.get("total_pages") or 1, MAX_PAGES)
    results = list(first.get("results", []))
    sem = asyncio.Semaphore(6)

    async def bounded(n):
        async with sem:
            return await page(n)

    for data in await asyncio.gather(*(bounded(n) for n in range(2, pages + 1))):
        results.extend(data.get("results", []))
    return results


def _year(date: str | None):
    return int(date[:4]) if date and date[:4].isdigit() else None


async def sync_streaming(session) -> dict:
    """Replace the stored catalogs. A service whose fetch fails keeps its old rows."""
    if not settings.tmdb_api_key:
        return {"error": "TMDB_API_KEY is not set"}
    summary: dict = {}
    async with httpx.AsyncClient(timeout=20) as client:
        ids = await _provider_ids(client)
        for name in STREAMING_SERVICES:
            if name not in ids:
                summary[name] = {"error": "TMDB has no provider by that name for the US"}
                continue
            try:
                films = await _catalog(client, ids[name])
            except (httpx.HTTPError, RuntimeError) as e:
                summary[name] = {"error": str(e)}
                continue
            await session.execute(delete(StreamingTitle).where(StreamingTitle.service == name))
            seen = set()
            for f in films:
                if f.get("id") in seen:
                    continue
                seen.add(f["id"])
                session.add(StreamingTitle(service=name, tmdb_id=f["id"], title=f.get("title"),
                                           year=_year(f.get("release_date"))))
            summary[name] = {"films": len(seen)}
    meta = await session.get(AppMeta, "streaming_sync")
    value = json.dumps({"at": datetime.utcnow().isoformat(timespec="seconds"), "summary": summary})
    if meta:
        meta.value = value
    else:
        session.add(AppMeta(key="streaming_sync", value=value))
    await session.commit()
    return summary


async def last_sync(session) -> dict | None:
    meta = await session.get(AppMeta, "streaming_sync")
    return json.loads(meta.value) if meta else None


async def streaming_index(session) -> dict[int, list[str]]:
    """tmdb_id -> services it is streaming on."""
    out: dict[int, list[str]] = {}
    for row in (await session.execute(select(StreamingTitle))).scalars().all():
        out.setdefault(row.tmdb_id, []).append(row.service)
    return out


async def refresh_loop(get_engine, get_session):
    """Background task: refresh the catalogs when they are more than a day old."""
    await asyncio.sleep(60)   # let the app finish starting first
    while True:
        try:
            engine = await get_engine()
            async with await get_session(engine) as session:
                last = await last_sync(session)
                due = (not last or datetime.utcnow() - datetime.fromisoformat(last["at"]) > REFRESH_EVERY)
                if due and settings.tmdb_api_key:
                    await sync_streaming(session)
        except Exception as e:   # never let a network hiccup kill the loop
            print(f"[streaming] refresh failed: {e}", flush=True)
        await asyncio.sleep(3600)
