"""FastAPI server — REST API for the media library.

This is the foundation for the long-term mobile/visual app.

Endpoints:
    POST /releases/lookup          Look up metadata without saving
    POST /releases                 Add a release to the library
    GET  /releases                 List all releases
    GET  /releases/{id}            Get a single release
    PATCH /releases/{id}           Update a release
    DELETE /releases/{id}          Remove a release
    GET  /releases/search?q=...    Search the library
    GET  /export/csv               Download CSV for Google Sheets

Run with:
    uvicorn medialibrary.server:app --reload
"""

from __future__ import annotations

import csv
import io
from typing import Optional

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from medialibrary.database import init_db, get_session, Movie, PhysicalRelease
from medialibrary.metadata import enrich
from medialibrary.config import settings

app = FastAPI(
    title="Media Library API",
    description="Physical disc media library — metadata lookup and management.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Tighten this for production
    allow_methods=["*"],
    allow_headers=["*"],
)

_engine = None


async def get_engine():
    global _engine
    if _engine is None:
        _engine = await init_db()
    return _engine


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class LookupRequest(BaseModel):
    upc: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None
    label: Optional[str] = None
    bluray_com_id: Optional[int] = None
    tmdb_id: Optional[int] = None


class AddReleaseRequest(LookupRequest):
    set_name: Optional[str] = None
    notes: Optional[str] = None
    condition: Optional[str] = None


class UpdateReleaseRequest(BaseModel):
    # Movie fields
    title: Optional[str] = None
    year: Optional[int] = None
    director: Optional[str] = None
    runtime_minutes: Optional[int] = None
    mpaa_rating: Optional[str] = None
    genres: Optional[str] = None
    overview: Optional[str] = None
    letterboxd_rating: Optional[float] = None
    # Release fields
    format: Optional[str] = None
    label: Optional[str] = None
    region: Optional[str] = None
    physical_release_date: Optional[str] = None
    edition: Optional[str] = None
    set_name: Optional[str] = None
    disc_count: Optional[int] = None
    aspect_ratio: Optional[str] = None
    upc: Optional[str] = None
    cover_url: Optional[str] = None
    cover_url_back: Optional[str] = None
    notes: Optional[str] = None


class ReleaseResponse(BaseModel):
    id: int
    # Movie fields
    title: str
    year: Optional[int]
    director: Optional[str]
    runtime_minutes: Optional[int]
    mpaa_rating: Optional[str]
    genres: Optional[str]
    tmdb_id: Optional[int]
    imdb_id: Optional[str]
    # Release fields
    upc: Optional[str]
    bluray_com_id: Optional[int]
    format: Optional[str]
    label: Optional[str]
    region: Optional[str]
    physical_release_date: Optional[str]
    edition: Optional[str]
    set_name: Optional[str]
    disc_count: Optional[int]
    aspect_ratio: Optional[str]
    cover_url: Optional[str]
    cover_url_back: Optional[str]
    notes: Optional[str]
    overview: Optional[str]
    letterboxd_rating: Optional[float]

    model_config = {"from_attributes": True}


def _release_to_response(r: PhysicalRelease) -> dict:
    m = r.movie
    return {
        "id": r.id,
        "title": m.title if m else "",
        "year": m.year if m else None,
        "director": m.director if m else None,
        "runtime_minutes": m.runtime_minutes if m else None,
        "mpaa_rating": m.mpaa_rating if m else None,
        "genres": m.genres if m else None,
        "tmdb_id": m.tmdb_id if m else None,
        "imdb_id": m.imdb_id if m else None,
        "upc": r.upc,
        "bluray_com_id": r.bluray_com_id,
        "format": r.format,
        "label": r.label,
        "region": r.region,
        "physical_release_date": r.physical_release_date,
        "edition": r.edition,
        "set_name": r.set_name,
        "disc_count": r.disc_count,
        "aspect_ratio": r.aspect_ratio,
        "cover_url": r.cover_url,
        "cover_url_back": r.cover_url_back,
        "notes": r.notes,
        "overview": m.overview if m else None,
        "letterboxd_rating": m.letterboxd_rating if m else None,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/releases/lookup", summary="Look up metadata without saving")
async def lookup_release(req: LookupRequest):
    """Fetch enriched metadata for a disc. Useful before deciding to add it."""
    if not any([req.upc, req.title, req.bluray_com_id, req.tmdb_id]):
        raise HTTPException(400, "Provide at least one of: upc, title, bluray_com_id, tmdb_id")

    result = await enrich(
        upc=req.upc,
        title=req.title,
        year=req.year,
        label=req.label,
        bluray_com_id=req.bluray_com_id,
        tmdb_id=req.tmdb_id,
    )
    return result.to_dict()


@app.post("/releases", status_code=201, summary="Add a release to the library")
async def add_release(req: AddReleaseRequest):
    """Look up metadata and persist the release + movie to the database."""
    if not any([req.upc, req.title, req.bluray_com_id, req.tmdb_id]):
        raise HTTPException(400, "Provide at least one of: upc, title, bluray_com_id, tmdb_id")

    result = await enrich(
        upc=req.upc,
        title=req.title,
        year=req.year,
        label=req.label,
        bluray_com_id=req.bluray_com_id,
        tmdb_id=req.tmdb_id,
    )
    if req.set_name:
        result.set_name = req.set_name

    engine = await get_engine()
    async with await get_session(engine) as session:
        # Upsert Movie
        movie = None
        if result.tmdb_id:
            stmt = select(Movie).where(Movie.tmdb_id == result.tmdb_id)
            movie = (await session.execute(stmt)).scalar_one_or_none()

        if not movie:
            movie = Movie(
                title=result.title,
                year=result.year,
                director=result.director,
                runtime_minutes=result.runtime_minutes,
                mpaa_rating=result.mpaa_rating,
                tmdb_id=result.tmdb_id,
                imdb_id=result.imdb_id,
                overview=result.overview,
                genres=result.genres,
                letterboxd_rating=result.letterboxd_rating,
            )
            session.add(movie)
            await session.flush()
        else:
            movie.director = result.director or movie.director
            movie.runtime_minutes = result.runtime_minutes or movie.runtime_minutes
            movie.mpaa_rating = result.mpaa_rating or movie.mpaa_rating
            if result.letterboxd_rating is not None:
                movie.letterboxd_rating = result.letterboxd_rating

        # Upsert PhysicalRelease
        release = None
        if result.upc:
            stmt = select(PhysicalRelease).where(PhysicalRelease.upc == result.upc)
            release = (await session.execute(stmt)).scalar_one_or_none()

        if not release:
            release = PhysicalRelease(
                movie_id=movie.id,
                upc=result.upc,
                bluray_com_id=result.bluray_com_id,
                format=result.format,
                label=result.label,
                region=result.region,
                physical_release_date=result.physical_release_date,
                edition=result.edition,
                set_name=result.set_name or req.set_name,
                disc_count=result.disc_count,
                aspect_ratio=result.aspect_ratio,
                cover_url=result.cover_url,
                cover_url_back=result.cover_url_back,
                notes=req.notes,
            )
            session.add(release)
        else:
            release.cover_url = result.cover_url or release.cover_url
            release.label = result.label or release.label

        await session.commit()
        await session.refresh(release)
        await session.refresh(movie)
        release.movie = movie

    return _release_to_response(release)


@app.get("/releases", summary="List all releases")
async def list_releases(
    q: Optional[str] = Query(None, description="Search by title"),
    format: Optional[str] = Query(None),
    label: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
):
    engine = await get_engine()
    async with await get_session(engine) as session:
        stmt = select(PhysicalRelease).options(joinedload(PhysicalRelease.movie))
        if format:
            stmt = stmt.where(PhysicalRelease.format.ilike(f"%{format}%"))
        if label:
            stmt = stmt.where(PhysicalRelease.label.ilike(f"%{label}%"))
        if region:
            stmt = stmt.where(PhysicalRelease.region.ilike(f"%{region}%"))
        releases = (await session.execute(stmt)).scalars().all()

    results = [_release_to_response(r) for r in releases]

    if q:
        q_lower = q.lower()
        results = [r for r in results if q_lower in r["title"].lower()]

    return sorted(results, key=lambda r: r["title"])


@app.get("/releases/{release_id}", summary="Get a single release")
async def get_release(release_id: int):
    engine = await get_engine()
    async with await get_session(engine) as session:
        stmt = select(PhysicalRelease).where(
            PhysicalRelease.id == release_id
        ).options(joinedload(PhysicalRelease.movie))
        release = (await session.execute(stmt)).scalar_one_or_none()

    if not release:
        raise HTTPException(404, f"Release {release_id} not found")
    return _release_to_response(release)


@app.patch("/releases/{release_id}", summary="Update a release")
async def update_release(release_id: int, req: UpdateReleaseRequest):
    engine = await get_engine()
    async with await get_session(engine) as session:
        stmt = select(PhysicalRelease).where(
            PhysicalRelease.id == release_id
        ).options(joinedload(PhysicalRelease.movie))
        release = (await session.execute(stmt)).scalar_one_or_none()
        if not release:
            raise HTTPException(404, f"Release {release_id} not found")

        movie = release.movie
        movie_fields = {"title", "year", "director", "runtime_minutes", "mpaa_rating", "genres", "overview", "letterboxd_rating"}
        release_fields = {"format", "label", "region", "physical_release_date", "edition", "set_name", "disc_count", "aspect_ratio", "upc", "cover_url", "cover_url_back", "notes"}

        for field, value in req.model_dump(exclude_none=True).items():
            if field in movie_fields:
                setattr(movie, field, value)
            elif field in release_fields:
                setattr(release, field, value)

        await session.commit()
        await session.refresh(release)
        await session.refresh(movie)
        release.movie = movie

    return _release_to_response(release)


@app.delete("/releases/{release_id}", status_code=204, summary="Remove a release")
async def delete_release(release_id: int):
    engine = await get_engine()
    async with await get_session(engine) as session:
        stmt = select(PhysicalRelease).where(PhysicalRelease.id == release_id)
        release = (await session.execute(stmt)).scalar_one_or_none()
        if not release:
            raise HTTPException(404, f"Release {release_id} not found")
        await session.delete(release)
        await session.commit()


@app.get("/export/csv", summary="Download CSV for Google Sheets import")
async def export_csv():
    engine = await get_engine()
    async with await get_session(engine) as session:
        stmt = select(PhysicalRelease).options(joinedload(PhysicalRelease.movie))
        releases = (await session.execute(stmt)).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Film", "Box Art", "Year", "Director", "Format", "Label",
        "Region", "Set", "MPAA Rating", "Runtime (min)",
        "Physical Release Date", "Aspect Ratio", "UPC",
        "TMDB ID", "IMDb ID", "Letterboxd Rating",
    ])
    for r in sorted(releases, key=lambda x: x.movie.title if x.movie else ""):
        m = r.movie
        cover = r.cover_url or ""
        writer.writerow([
            m.title if m else "",
            f'=IMAGE("{cover}")' if cover else "",
            m.year if m else "",
            m.director if m else "",
            r.format or "",
            r.label or "",
            r.region or "",
            r.set_name or "",
            m.mpaa_rating if m else "",
            m.runtime_minutes if m else "",
            r.physical_release_date or "",
            r.aspect_ratio or "",
            r.upc or "",
            m.tmdb_id if m else "",
            m.imdb_id if m else "",
            m.letterboxd_rating if m else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=medialibrary.csv"},
    )


@app.post("/sync/letterboxd", summary="Sync Letterboxd ratings for all library movies")
async def sync_letterboxd(username: Optional[str] = Query(None)):
    """Fetch all ratings from a public Letterboxd profile and update matching movies.

    Uses LETTERBOXD_USERNAME from config if username query param is not provided.
    """
    from medialibrary.api.letterboxd import LetterboxdClient, _normalize
    from datetime import datetime

    lb_user = username or settings.letterboxd_username
    if not lb_user:
        raise HTTPException(400, "Provide ?username= or set LETTERBOXD_USERNAME in .env")

    lb_client = LetterboxdClient()
    try:
        ratings = await lb_client.get_all_ratings(lb_user)
    except Exception as e:
        raise HTTPException(502, f"Letterboxd fetch failed: {e}")

    index = lb_client.build_index(ratings)

    engine = await get_engine()
    async with await get_session(engine) as session:
        movies = (await session.execute(select(Movie))).scalars().all()
        matched = 0
        now = datetime.utcnow()
        for movie in movies:
            key = (_normalize(movie.title), movie.year)
            if key in index:
                movie.letterboxd_rating = index[key]
                movie.letterboxd_synced_at = now
                matched += 1
        await session.commit()

    return {
        "letterboxd_username": lb_user,
        "ratings_fetched": len(ratings),
        "library_titles": len(movies),
        "matched": matched,
    }


@app.get("/debug/letterboxd/{release_id}", summary="Debug Letterboxd scraping for a release")
async def debug_letterboxd(release_id: int):
    """Show exactly what the scraper sees for a release — use to troubleshoot rating issues."""
    import httpx
    from bs4 import BeautifulSoup
    from medialibrary.api.letterboxd import LetterboxdClient, _normalize, _title_to_slug

    engine = await get_engine()
    async with await get_session(engine) as session:
        stmt = select(PhysicalRelease).where(
            PhysicalRelease.id == release_id
        ).options(joinedload(PhysicalRelease.movie))
        release = (await session.execute(stmt)).scalar_one_or_none()
        if not release:
            raise HTTPException(404, "Not found")
        movie = release.movie

    lb_user = settings.letterboxd_username
    lb = LetterboxdClient()

    # RSS check
    ratings = await lb.get_all_ratings(lb_user)
    norm_title = _normalize(movie.title)
    rss_matches = [
        {"title": r.title, "year": r.year, "rating": r.rating, "tmdb_id": r.tmdb_id}
        for r in ratings
        if r.tmdb_id == movie.tmdb_id or _normalize(r.title) == norm_title
    ]

    slug = _title_to_slug(movie.title)
    pages = []
    for path in ["diary/", "reviews/", ""]:
        url = f"{lb.BASE}/{lb_user}/film/{slug}/{path}"
        async with httpx.AsyncClient(headers=lb._HEADERS, follow_redirects=True, timeout=10) as client:
            try:
                resp = await client.get(url)
                status = resp.status_code
                html = resp.text if status == 200 else ""
            except Exception as e:
                status = 0
                html = str(e)

        soup = BeautifulSoup(html, "html.parser") if html else None
        content = soup.select_one("div#content") if soup else None

        twitter_meta  = soup.find("meta", attrs={"name": "twitter:data2"}) if soup else None
        rateit_input = content.select_one("input.rateit-field[type='range']") if content else None
        svg_glyph    = content.select_one("svg.glyph.-rating") if content else None
        span_rating  = content.select_one("span.rating") if content else None

        parsed_rating = lb._parse_rating_from_page(soup) if soup else None

        pages.append({
            "url": url,
            "status": status,
            "parsed_rating": parsed_rating,
            "twitter_data2": twitter_meta.get("content") if twitter_meta else None,
            "rateit_input_value": rateit_input.get("value") if rateit_input else None,
            "svg_glyph_aria_label": svg_glyph.get("aria-label") if svg_glyph else None,
            "span_rating_text": span_rating.get_text(strip=True) if span_rating else None,
        })

    # Probe numbered review pages
    numbered = []
    for n in range(1, 6):
        url = f"{lb.BASE}/{lb_user}/film/{slug}/{n}/"
        async with httpx.AsyncClient(headers=lb._HEADERS, follow_redirects=True, timeout=10) as client:
            try:
                resp = await client.get(url)
                status = resp.status_code
                html = resp.text if status == 200 else ""
            except Exception as e:
                status = 0
                html = ""

        if status == 404:
            numbered.append({"url": url, "status": 404, "parsed_rating": None})
            break

        soup = BeautifulSoup(html, "html.parser") if html else None
        twitter_meta = soup.find("meta", attrs={"name": "twitter:data2"}) if soup else None
        parsed = lb._parse_rating_from_page(soup) if soup else None

        numbered.append({
            "url": url,
            "status": status,
            "parsed_rating": parsed,
            "twitter_data2": twitter_meta.get("content") if twitter_meta else None,
        })

    return {
        "movie": {"title": movie.title, "year": movie.year, "tmdb_id": movie.tmdb_id},
        "slug": slug,
        "rss_total": len(ratings),
        "rss_matches": rss_matches,
        "pages": pages,
        "numbered_pages": numbered,
        "final_rating_would_be": next(
            (p["parsed_rating"] for p in reversed(numbered) if p.get("parsed_rating") is not None),
            next((p["parsed_rating"] for p in pages if p.get("parsed_rating") is not None), None)
        ),
    }


@app.post("/releases/{release_id}/refresh-letterboxd", summary="Refresh Letterboxd rating for one release")
async def refresh_letterboxd_rating(release_id: int):
    """Re-fetch the configured user's Letterboxd rating for this specific release.

    Uses the two-tier strategy (RSS feed + direct film page scrape) so it works
    for both recent and older ratings.
    """
    from medialibrary.api.letterboxd import LetterboxdClient

    lb_user = settings.letterboxd_username
    if not lb_user:
        raise HTTPException(400, "LETTERBOXD_USERNAME not set in .env")

    engine = await get_engine()
    async with await get_session(engine) as session:
        stmt = select(PhysicalRelease).where(
            PhysicalRelease.id == release_id
        ).options(joinedload(PhysicalRelease.movie))
        release = (await session.execute(stmt)).scalar_one_or_none()
        if not release:
            raise HTTPException(404, f"Release {release_id} not found")

        movie = release.movie
        lb_client = LetterboxdClient()
        rating = await lb_client.get_rating_for_film(
            lb_user,
            title=movie.title,
            year=movie.year,
            tmdb_id=movie.tmdb_id,
        )

        if rating is not None:
            movie.letterboxd_rating = rating
            await session.commit()
            await session.refresh(release)
            await session.refresh(movie)
            release.movie = movie

    return _release_to_response(release)


@app.get("/bluray/search-covers", summary="Search blu-ray.com and return cover art options")
async def search_covers(
    title: str = Query(...),
    year: Optional[int] = Query(None),
):
    from medialibrary.api.bluray import BlurayClient
    client = BlurayClient()
    results = await client.search(title, year)
    if not results and year:
        results = await client.search(title)
    return [
        {
            "bluray_com_id": r["bluray_com_id"],
            "title": r["title"],
            "year": r.get("year"),
            "cover_url": r["cover_url"],
            "detail_url": r.get("detail_url", ""),
        }
        for r in results[:12]
    ]


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Frontend (must be last) ───────────────────────────────────────────────────
_FRONTEND = Path(__file__).parent.parent / "frontend"

if _FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_ui():
        return FileResponse(str(_FRONTEND / "index.html"))
