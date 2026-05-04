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
import json
from typing import Optional

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
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
    bluray_com_url: Optional[str] = None
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
    films_included: Optional[list] = None


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
    films_included: Optional[list] = None

    model_config = {"from_attributes": True}


def _parse_films(raw: str | None) -> list:
    """Deserialise films_included from JSON string stored in DB."""
    if not raw:
        return []
    import json
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except Exception:
        return []


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
        "films_included": _parse_films(r.films_included),
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
        bluray_com_url=req.bluray_com_url,
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
        bluray_com_url=req.bluray_com_url,
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
        if not release and result.bluray_com_id:
            stmt = select(PhysicalRelease).where(PhysicalRelease.bluray_com_id == result.bluray_com_id)
            release = (await session.execute(stmt)).scalar_one_or_none()

        if not release:
            release = PhysicalRelease(
                movie_id=movie.id,
                upc=result.upc or None,
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
                films_included=json.dumps(result.films_included) if result.films_included else None,
            )
            session.add(release)
        else:
            release.cover_url = result.cover_url or release.cover_url
            release.label = result.label or release.label
            if result.films_included:
                release.films_included = json.dumps(result.films_included)

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
        def _matches(r: dict) -> bool:
            if q_lower in (r.get("title") or "").lower():
                return True
            for film in (r.get("films_included") or []):
                film_title = film.get("title", "") if isinstance(film, dict) else film
                if q_lower in film_title.lower():
                    return True
            return False
        results = [r for r in results if _matches(r)]

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
            elif field == "films_included":
                release.films_included = json.dumps(value) if value else None

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


@app.post("/releases/{release_id}/re-enrich", summary="Re-run full enrichment pipeline for an existing release")
async def re_enrich_release(release_id: int):
    """Re-fetch all metadata (TMDB, Blu-ray.com, Letterboxd) and overwrite the record.

    Useful after enrichment bug-fixes so existing entries don't need to be
    deleted and re-scanned.
    """
    engine = await get_engine()
    async with await get_session(engine) as session:
        stmt = select(PhysicalRelease).where(
            PhysicalRelease.id == release_id
        ).options(joinedload(PhysicalRelease.movie))
        release = (await session.execute(stmt)).scalar_one_or_none()
        if not release:
            raise HTTPException(404, f"Release {release_id} not found")
        movie = release.movie

    result = await enrich(
        upc=release.upc or None,
        title=movie.title if movie else None,
        year=movie.year if movie else None,
        label=release.label or None,
        bluray_com_id=release.bluray_com_id or None,
        tmdb_id=movie.tmdb_id if movie else None,
    )

    async with await get_session(engine) as session:
        stmt = select(PhysicalRelease).where(
            PhysicalRelease.id == release_id
        ).options(joinedload(PhysicalRelease.movie))
        release = (await session.execute(stmt)).scalar_one_or_none()
        movie = release.movie

        # Update movie fields
        movie.title = result.title or movie.title
        movie.year = result.year or movie.year
        movie.director = result.director or movie.director
        movie.runtime_minutes = result.runtime_minutes or movie.runtime_minutes
        movie.mpaa_rating = result.mpaa_rating or movie.mpaa_rating
        movie.genres = result.genres or movie.genres
        movie.overview = result.overview or movie.overview
        if result.tmdb_id:
            movie.tmdb_id = result.tmdb_id
        if result.imdb_id:
            movie.imdb_id = result.imdb_id
        if result.letterboxd_rating is not None:
            movie.letterboxd_rating = result.letterboxd_rating

        # Update release fields
        release.format = result.format or release.format
        release.label = result.label or release.label
        release.region = result.region or release.region
        release.physical_release_date = result.physical_release_date or release.physical_release_date
        release.edition = result.edition or release.edition
        release.disc_count = result.disc_count or release.disc_count
        release.aspect_ratio = result.aspect_ratio or release.aspect_ratio
        if result.cover_url:
            release.cover_url = result.cover_url
        if result.cover_url_back:
            release.cover_url_back = result.cover_url_back
        if result.bluray_com_id:
            release.bluray_com_id = result.bluray_com_id
        if result.films_included:
            release.films_included = json.dumps(result.films_included)

        await session.commit()
        await session.refresh(release)
        await session.refresh(movie)
        release.movie = movie

    return _release_to_response(release)


@app.get("/debug/tmdb-search", summary="Show raw TMDB search results for a query")
async def debug_tmdb_search(q: str = Query(...), year: Optional[int] = Query(None)):
    """Diagnostic: returns the raw TMDB candidate list and which result _pick_best selects."""
    from medialibrary.api.tmdb import TMDBClient, _pick_best, _norm
    tmdb = TMDBClient()
    results = await tmdb.search_movie(q, year)
    if not results and year:
        results = await tmdb.search_movie(q, None)
    best = _pick_best(results, q) if results else None
    return {
        "query": q,
        "year": year,
        "query_norm": _norm(q),
        "results": [
            {
                "id": r.get("id"),
                "title": r.get("title"),
                "title_norm": _norm(r.get("title", "")),
                "release_date": r.get("release_date", "")[:4],
                "popularity": r.get("popularity"),
                "exact_match": _norm(r.get("title", "")) == _norm(q),
            }
            for r in results[:10]
        ],
        "pick_best_id": best.get("id") if best else None,
        "pick_best_title": best.get("title") if best else None,
    }


@app.get("/debug/bluray-upc", summary="Show raw Blu-ray.com data for a UPC")
async def debug_bluray_upc(upc: str = Query(...)):
    """Diagnostic: shows exactly what our Blu-ray.com scraper returns for a UPC,
    including the raw films_included list before any TMDB enrichment."""
    from medialibrary.api.bluray import BlurayClient
    from medialibrary.metadata import _normalize_lookup_title
    client = BlurayClient()
    candidates = await client.search(upc)
    if not candidates:
        return {"upc": upc, "candidates": [], "detail": None}
    stub = candidates[0]
    detail = await client.get_release(stub["bluray_com_id"], stub.get("detail_url"))
    films = (detail or {}).get("films_included", [])
    return {
        "upc": upc,
        "stub": stub,
        "detail_title": (detail or {}).get("title"),
        "film_year": (detail or {}).get("film_year"),
        "films_included_raw": films,
        "films_included_normalized": [_normalize_lookup_title(f) for f in films],
    }


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


@app.post("/import/csv", summary="Bulk import releases from a CSV file")
async def import_csv(file: UploadFile = File(...)):
    """Import multiple releases from a CSV file.

    Flexible column mapping — understands common header names from Google Sheets
    or the app's own export format. Skips rows that already exist (matched by
    title+year or UPC). Returns a summary with per-row errors.

    Required: a column containing the film title (title / film / movie / name).
    Optional: year, format, label, region, set / set_name, edition, notes,
              upc / barcode, tmdb_id, bluray_com_id, condition.
    """
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # strip BOM if present
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(400, "CSV has no headers")

    # Normalise header names to lowercase-stripped keys
    def _norm(h: str) -> str:
        return h.strip().lower().replace(" ", "_").replace("-", "_")

    # Map of normalised header → field name we care about
    _TITLE_KEYS   = {"title", "film", "movie", "name"}
    _YEAR_KEYS    = {"year"}
    _FORMAT_KEYS  = {"format", "disc_format"}
    _LABEL_KEYS   = {"label", "studio", "distributor", "publisher"}
    _REGION_KEYS  = {"region"}
    _SET_KEYS     = {"set", "set_name", "collection", "box_set"}
    _EDITION_KEYS = {"edition"}
    _NOTES_KEYS   = {"notes", "note", "comments"}
    _UPC_KEYS     = {"upc", "barcode", "ean"}
    _TMDB_KEYS    = {"tmdb_id", "tmdb"}
    _BLURAY_KEYS  = {"bluray_com_id", "bluray_id", "bluray.com_id"}
    _CONDITION_KEYS = {"condition"}

    def _pick(row: dict, keys: set) -> str:
        for k, v in row.items():
            if _norm(k) in keys and v and str(v).strip():
                return str(v).strip()
        return ""

    engine = await get_engine()

    imported = 0
    skipped  = 0
    failed   = 0
    errors: list[dict] = []

    rows = list(reader)
    for i, raw_row in enumerate(rows, start=2):  # row 2 = first data row
        title = _pick(raw_row, _TITLE_KEYS)
        if not title or title.startswith("="):
            skipped += 1
            continue

        year_str = _pick(raw_row, _YEAR_KEYS)
        try:
            year: Optional[int] = int(float(year_str)) if year_str else None
        except ValueError:
            year = None

        upc    = _pick(raw_row, _UPC_KEYS) or None
        label  = _pick(raw_row, _LABEL_KEYS) or None
        fmt    = _pick(raw_row, _FORMAT_KEYS) or None
        region = _pick(raw_row, _REGION_KEYS) or None
        set_nm = _pick(raw_row, _SET_KEYS) or None
        edition = _pick(raw_row, _EDITION_KEYS) or None
        notes  = _pick(raw_row, _NOTES_KEYS) or None
        condition = _pick(raw_row, _CONDITION_KEYS) or None

        tmdb_str = _pick(raw_row, _TMDB_KEYS)
        try:
            tmdb_id: Optional[int] = int(float(tmdb_str)) if tmdb_str else None
        except ValueError:
            tmdb_id = None

        bluray_str = _pick(raw_row, _BLURAY_KEYS)
        try:
            bluray_com_id: Optional[int] = int(float(bluray_str)) if bluray_str else None
        except ValueError:
            bluray_com_id = None

        try:
            result = await enrich(
                upc=upc,
                title=title,
                year=year,
                label=label,
                bluray_com_id=bluray_com_id,
                tmdb_id=tmdb_id,
            )
        except Exception as exc:
            failed += 1
            errors.append({"row": i, "title": title, "error": str(exc)})
            continue

        # Override enrich defaults with CSV-supplied values when present
        if fmt:
            result.format = fmt
        if set_nm:
            result.set_name = set_nm
        if edition:
            result.edition = edition

        try:
            async with await get_session(engine) as session:
                # Duplicate check: same title+year already in library
                dup_stmt = (
                    select(PhysicalRelease)
                    .join(PhysicalRelease.movie)
                    .where(Movie.title.ilike(result.title or title))
                )
                if year or result.year:
                    dup_stmt = dup_stmt.where(Movie.year == (result.year or year))
                dup = (await session.execute(dup_stmt)).scalar_one_or_none()
                if dup:
                    skipped += 1
                    continue

                # Upsert Movie
                movie = None
                if result.tmdb_id:
                    stmt = select(Movie).where(Movie.tmdb_id == result.tmdb_id)
                    movie = (await session.execute(stmt)).scalar_one_or_none()

                if not movie:
                    movie = Movie(
                        title=result.title or title,
                        year=result.year or year,
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

                release = PhysicalRelease(
                    movie_id=movie.id,
                    upc=result.upc or upc,
                    bluray_com_id=result.bluray_com_id or bluray_com_id,
                    format=result.format,
                    label=result.label or label,
                    region=result.region or region,
                    physical_release_date=result.physical_release_date,
                    edition=result.edition,
                    set_name=result.set_name,
                    disc_count=result.disc_count,
                    aspect_ratio=result.aspect_ratio,
                    cover_url=result.cover_url,
                    cover_url_back=result.cover_url_back,
                    notes=notes,
                    condition=condition,
                )
                session.add(release)
                await session.commit()
                imported += 1

        except Exception as exc:
            failed += 1
            errors.append({"row": i, "title": title, "error": str(exc)})

    return {
        "total_rows": len(rows),
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "errors": errors[:50],  # cap to avoid huge responses
    }


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

    @app.get("/scan", include_in_schema=False)
    async def serve_scan():
        return FileResponse(str(_FRONTEND / "scan.html"))
