"""Metadata orchestrator.

Combines UPC lookup → TMDB movie metadata → Blu-ray.com physical release
details into a single unified record.

Usage:
    result = await enrich(upc="025192251498")
    result = await enrich(title="Alien", year=1979, label="20th Century Fox")
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, asdict
from typing import Any

from medialibrary.api.tmdb import TMDBClient
from medialibrary.api.upc import UPCClient
from medialibrary.api.bluray import BlurayClient
from medialibrary.api.letterboxd import LetterboxdClient
from medialibrary.config import settings


@dataclass
class EnrichedRelease:
    """Unified record combining movie + physical release metadata."""

    # Movie metadata (from TMDB)
    title: str = ""
    year: int | None = None
    director: str = ""
    runtime_minutes: int | None = None
    mpaa_rating: str = ""
    genres: str = ""
    overview: str = ""
    tmdb_id: int | None = None
    imdb_id: str = ""
    poster_url: str = ""          # TMDB poster (fallback when no disc cover)

    # Physical release metadata (from Blu-ray.com)
    upc: str = ""
    bluray_com_id: int | None = None
    format: str = ""              # Blu-Ray, UHD, DVD
    label: str = ""               # Criterion, Arrow, A24…
    region: str = ""              # US, A, B, Free…
    physical_release_date: str = ""
    edition: str = ""             # e.g. "Criterion Spine #42"
    set_name: str = ""            # e.g. "Alien Anthology"
    disc_count: int | None = None
    aspect_ratio: str = ""
    cover_url: str = ""           # Blu-ray.com front cover
    cover_url_back: str = ""
    films_included: list[str] = field(default_factory=list)  # titles in a box set

    # Personal rating (from Letterboxd)
    letterboxd_rating: float | None = None  # 0.5 – 5.0, None if not rated / not fetched

    # Source tracking
    sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def cover_image_formula(self) -> str:
        """Google Sheets =IMAGE() formula using the best available cover art."""
        url = self.cover_url or self.poster_url
        if url:
            return f'=IMAGE("{url}")'
        return ""

    def letterboxd_stars(self) -> str:
        """Convert numeric rating to half-star display (e.g. 4.5 → '★★★★½')."""
        if self.letterboxd_rating is None:
            return ""
        full = int(self.letterboxd_rating)
        half = (self.letterboxd_rating - full) >= 0.5
        return "★" * full + ("½" if half else "")

    def summary(self) -> str:
        lb = f" ({self.letterboxd_stars()})" if self.letterboxd_rating else ""
        lines = [
            f"  Title:    {self.title} ({self.year})",
            f"  Director: {self.director}",
            f"  Runtime:  {self.runtime_minutes} min" if self.runtime_minutes else "",
            f"  Rating:   {self.mpaa_rating}" if self.mpaa_rating else "",
            f"  Letterboxd: {self.letterboxd_rating}{lb}" if self.letterboxd_rating else "",
            f"  Format:   {self.format}" if self.format else "",
            f"  Label:    {self.label}" if self.label else "",
            f"  Region:   {self.region}" if self.region else "",
            f"  Released: {self.physical_release_date}" if self.physical_release_date else "",
            f"  Edition:  {self.edition}" if self.edition else "",
            f"  UPC:      {self.upc}" if self.upc else "",
            f"  Cover:    {self.cover_url or self.poster_url}",
        ]
        return "\n".join(l for l in lines if l)


async def enrich(
    upc: str | None = None,
    title: str | None = None,
    year: int | None = None,
    label: str | None = None,
    bluray_com_id: int | None = None,
    tmdb_id: int | None = None,
) -> EnrichedRelease:
    """Main entry point. Provide at least one of: upc, title, bluray_com_id, tmdb_id."""

    result = EnrichedRelease()
    tmdb = TMDBClient()
    upc_client = UPCClient()
    bluray = BlurayClient()

    # ── Step 1: UPC lookup ───────────────────────────────────────────────────
    if upc:
        result.upc = upc
        try:
            upc_data = await upc_client.lookup(upc)
            if upc_data:
                result.sources.append("upcitemdb")
                if not title:
                    title = upc_data.get("search_title") or upc_data.get("raw_title")
                if not year:
                    year = upc_data.get("year_hint")
                if not result.format:
                    result.format = upc_data.get("format", "")
                if not label:
                    label = upc_data.get("brand", "")
            else:
                result.warnings.append(f"UPC {upc} not found in UPCitemdb")
        except Exception as e:
            result.warnings.append(f"UPC lookup failed: {e}")

    # ── Step 2: TMDB movie metadata ──────────────────────────────────────────
    tmdb_data: dict | None = None
    if tmdb_id:
        try:
            tmdb_data = await tmdb.lookup_by_tmdb_id(tmdb_id)
            result.sources.append("tmdb")
        except Exception as e:
            result.warnings.append(f"TMDB lookup by ID failed: {e}")

    if tmdb_data is None and title:
        try:
            # Strip disc format indicators before searching TMDB so
            # "Blow Out 4K" finds "Blow Out", "Se7en 4K UHD" finds "Se7en", etc.
            import re as _re
            tmdb_title = _re.sub(
                r'\s*\b(4K|UHD|Ultra\s*HD|Blu[- ]?ray|BD)\b.*$',
                '', title, flags=_re.IGNORECASE,
            ).strip() or title
            tmdb_data = await tmdb.lookup(tmdb_title, year)
            if tmdb_data:
                result.sources.append("tmdb")
        except ValueError as e:
            result.warnings.append(str(e))
        except Exception as e:
            result.warnings.append(f"TMDB lookup failed: {e}")

    if tmdb_data:
        result.title = tmdb_data.get("title", title or "")
        result.year = tmdb_data.get("year")
        result.director = tmdb_data.get("director", "")
        result.runtime_minutes = tmdb_data.get("runtime_minutes")
        result.mpaa_rating = tmdb_data.get("mpaa_rating", "")
        result.genres = tmdb_data.get("genres", "")
        result.overview = tmdb_data.get("overview", "")
        result.tmdb_id = tmdb_data.get("tmdb_id")
        result.imdb_id = tmdb_data.get("imdb_id", "")
        result.poster_url = tmdb_data.get("poster_url", "")
    elif title:
        result.title = title
        result.year = year

    # ── Step 3: Blu-ray.com physical release details ─────────────────────────
    bluray_data: dict | None = None
    if bluray_com_id:
        try:
            bluray_data = await bluray.get_release(bluray_com_id)
            result.sources.append("bluray.com")
        except Exception as e:
            result.warnings.append(f"Blu-ray.com fetch by ID failed: {e}")
    elif result.title:
        try:
            bluray_data = await bluray.search_and_get_best(
                result.title, result.year, label
            )
            if bluray_data:
                result.sources.append("bluray.com")
        except Exception as e:
            result.warnings.append(f"Blu-ray.com search failed: {e}")

    if bluray_data:
        result.bluray_com_id = bluray_data.get("bluray_com_id")
        result.cover_url = bluray_data.get("cover_url", "")
        result.cover_url_back = bluray_data.get("cover_url_back", "")
        result.physical_release_date = bluray_data.get("physical_release_date", "")
        result.label = bluray_data.get("label", label or "")
        result.region = bluray_data.get("region", "")
        result.edition = bluray_data.get("edition", "")
        result.disc_count = bluray_data.get("disc_count")
        result.aspect_ratio = bluray_data.get("aspect_ratio", "")

        # Use Blu-ray.com runtime as fallback
        if not result.runtime_minutes and bluray_data.get("runtime_minutes"):
            result.runtime_minutes = bluray_data["runtime_minutes"]

        # Use Blu-ray.com UPC if we don't have one from scan
        if not result.upc and bluray_data.get("upc"):
            result.upc = bluray_data["upc"]

        # Override format from Blu-ray.com (more reliable than UPC description)
        if bluray_data.get("format"):
            result.format = bluray_data["format"]

        if bluray_data.get("films_included"):
            result.films_included = bluray_data["films_included"]

    # ── Step 4: Letterboxd personal rating ───────────────────────────────────
    if settings.letterboxd_username and result.title:
        try:
            lb_client = LetterboxdClient()
            rating = await lb_client.get_rating_for_film(
                settings.letterboxd_username,
                result.title,
                result.year,
                tmdb_id=result.tmdb_id,
            )
            if rating is not None:
                result.letterboxd_rating = rating
                result.sources.append("letterboxd")
        except Exception as e:
            result.warnings.append(f"Letterboxd lookup failed: {e}")

    return result
