"""TMDB (The Movie Database) API client.

Fetches movie metadata: title, year, director, runtime, MPAA rating, genres,
overview, and poster/backdrop images.

Free API key: https://www.themoviedb.org/settings/api
"""

from __future__ import annotations

import httpx
from typing import Any

from medialibrary.config import settings

# Superscript → space+digit mapping mirrors metadata._normalize_lookup_title
_SUPERSCRIPT = {"⁰": " 0", "¹": " 1", "²": " 2", "³": " 3", "⁴": " 4",
                "⁵": " 5", "⁶": " 6", "⁷": " 7", "⁸": " 8", "⁹": " 9"}


def _norm(t: str) -> str:
    for sup, rep in _SUPERSCRIPT.items():
        t = t.replace(sup, rep)
    return t.lower().strip()


def _pick_best(results: list[dict], query: str) -> dict:
    """Return the result whose title best matches *query*, not just the most popular.

    TMDB ranks by popularity so 'Alien' (1979) outranks 'Alien³' when searching
    'Alien 3'. We re-rank by title similarity first.
    """
    q = _norm(query)
    # 1. Exact normalized match
    for r in results:
        if _norm(r.get("title", "")) == q:
            return r
    # 2. Result title starts with query (e.g. partial match)
    for r in results:
        if _norm(r.get("title", "")).startswith(q):
            return r
    # 3. Fall back to popularity (first result)
    return results[0]


class TMDBClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.tmdb_api_key
        self.base = settings.tmdb_base_url
        self.image_base = settings.tmdb_image_base

    def _params(self, **kwargs) -> dict:
        return {"api_key": self.api_key, **kwargs}

    async def search_movie(self, title: str, year: int | None = None) -> list[dict]:
        """Search for a movie by title. Returns list of candidates."""
        params = self._params(query=title, include_adult=False)
        if year:
            params["year"] = year
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{self.base}/search/movie", params=params)
            r.raise_for_status()
            return r.json().get("results", [])

    async def get_movie_details(self, tmdb_id: int) -> dict:
        """Fetch full movie details including credits and release_dates."""
        params = self._params(append_to_response="credits,release_dates,videos")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{self.base}/movie/{tmdb_id}", params=params)
            r.raise_for_status()
            return r.json()

    async def find_by_imdb_id(self, imdb_id: str) -> dict | None:
        """Look up a movie using its IMDb ID (e.g. 'tt0083658')."""
        params = self._params(external_source="imdb_id")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{self.base}/find/{imdb_id}", params=params)
            r.raise_for_status()
            results = r.json().get("movie_results", [])
            return results[0] if results else None

    def parse_metadata(self, details: dict) -> dict:
        """Extract the fields we care about from a full TMDB movie details response."""
        # Director from credits
        director = ""
        for crew in details.get("credits", {}).get("crew", []):
            if crew.get("job") == "Director":
                director = crew.get("name", "")
                break

        # MPAA rating (US certification)
        mpaa_rating = ""
        for country in details.get("release_dates", {}).get("results", []):
            if country.get("iso_3166_1") == "US":
                for rd in country.get("release_dates", []):
                    cert = rd.get("certification", "")
                    if cert:
                        mpaa_rating = cert
                        break
                break

        # Genres as comma-separated string
        genres = ", ".join(g["name"] for g in details.get("genres", []))

        # Poster URL
        poster_path = details.get("poster_path", "")
        poster_url = f"{self.image_base}{poster_path}" if poster_path else ""

        return {
            "tmdb_id": details.get("id"),
            "imdb_id": details.get("imdb_id", ""),
            "title": details.get("title", ""),
            "year": int(details.get("release_date", "0")[:4]) if details.get("release_date") else None,
            "director": director,
            "runtime_minutes": details.get("runtime"),
            "mpaa_rating": mpaa_rating,
            "overview": details.get("overview", ""),
            "genres": genres,
            "poster_url": poster_url,
            "tagline": details.get("tagline", ""),
        }

    async def lookup(self, title: str, year: int | None = None) -> dict | None:
        """Convenience: search + fetch details for the best matching movie."""
        if not self.api_key:
            raise ValueError("TMDB_API_KEY not set. Get one free at https://www.themoviedb.org/settings/api")
        results = await self.search_movie(title, year)
        # If no results with year, retry without it (UPC year hints are unreliable)
        if not results and year is not None:
            results = await self.search_movie(title, None)
        if not results:
            return None
        best = _pick_best(results, title)
        details = await self.get_movie_details(best["id"])
        return self.parse_metadata(details)

    async def lookup_by_tmdb_id(self, tmdb_id: int) -> dict | None:
        """Fetch and parse a movie directly by TMDB ID."""
        if not self.api_key:
            raise ValueError("TMDB_API_KEY not set.")
        details = await self.get_movie_details(tmdb_id)
        return self.parse_metadata(details)
