"""Letterboxd ratings scraper.

Two usage patterns:

1. Single-film lookup at add time (2 HTTP requests):
       client = LetterboxdClient()
       rating = await client.get_rating_for_film("username", "Alien", 1979)
       # → 4.5 or None

2. Bulk sync (paginated scrape of all rated films):
       ratings = await client.get_all_ratings("username")
       index = client.build_index(ratings)
       # index[("alien", 1979)] → 4.5
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import httpx
from bs4 import BeautifulSoup


@dataclass
class LetterboxdRating:
    title: str
    year: Optional[int]
    rating: float   # 0.5 – 5.0 in half-star increments
    slug: str       # e.g. "alien-1979"


def _normalize(title: str) -> str:
    """Lowercase, strip punctuation for fuzzy title matching."""
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def _parse_rated_class(classes: list[str]) -> Optional[float]:
    """Convert a 'rated-N' CSS class (N=1..10) to a 0.5..5.0 star value."""
    for cls in classes:
        if cls.startswith("rated-"):
            try:
                return int(cls.split("-")[1]) / 2.0
            except (ValueError, IndexError):
                pass
    return None


class LetterboxdClient:
    BASE = "https://letterboxd.com"
    _HEADERS = {
        "User-Agent": (
            "medialibrary-metadata-tool/1.0 "
            "(+https://github.com/user/medialibrary)"
        )
    }

    # ── Single-film lookup ────────────────────────────────────────────────────

    async def get_rating_for_film(
        self,
        username: str,
        title: str,
        year: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> Optional[float]:
        """Return the user's star rating (0.5-5.0) for a specific film, or None.

        Tries slug matching via Letterboxd search (2 HTTP requests).
        If imdb_id is provided it can serve as a fallback slug hint.
        """
        slug = await self._find_film_slug(title, year)
        if not slug:
            return None
        return await self._get_user_film_rating(username, slug)

    async def _find_film_slug(
        self,
        title: str,
        year: Optional[int],
    ) -> Optional[str]:
        """Search Letterboxd for a film and return its slug."""
        query = title.strip().replace(" ", "+")
        url = f"{self.BASE}/search/films/{query}/"
        async with httpx.AsyncClient(
            headers=self._HEADERS, follow_redirects=True
        ) as client:
            try:
                resp = await client.get(url, timeout=10)
                resp.raise_for_status()
            except httpx.HTTPError:
                return None

        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select("li.search-result .film-poster"):
            slug = item.get("data-film-slug", "")
            item_year_raw = item.get("data-film-year", "")
            item_year = int(item_year_raw) if item_year_raw.isdigit() else None

            if year and item_year and abs(item_year - year) > 1:
                continue  # skip if year is off by more than 1 (re-releases aside)

            if slug:
                return slug

        return None

    async def _get_user_film_rating(
        self, username: str, slug: str
    ) -> Optional[float]:
        """Fetch the user's personal film page and extract their rating."""
        url = f"{self.BASE}/{username}/film/{slug}/"
        async with httpx.AsyncClient(
            headers=self._HEADERS, follow_redirects=True
        ) as client:
            try:
                resp = await client.get(url, timeout=10)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
            except httpx.HTTPError:
                return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Letterboxd renders the user's own rating in a <span class="rating rated-N">
        rating_span = soup.select_one("span.rating")
        if rating_span:
            return _parse_rated_class(rating_span.get("class", []))

        return None

    # ── Bulk ratings scrape ───────────────────────────────────────────────────

    async def get_all_ratings(self, username: str) -> list[LetterboxdRating]:
        """Scrape all rated films from /{username}/films/ratings/ (paginated)."""
        ratings: list[LetterboxdRating] = []
        page = 1

        async with httpx.AsyncClient(
            headers=self._HEADERS, follow_redirects=True
        ) as client:
            while True:
                url = f"{self.BASE}/{username}/films/ratings/page/{page}/"
                try:
                    resp = await client.get(url, timeout=15)
                except httpx.HTTPError:
                    break

                if resp.status_code == 404:
                    break
                resp.raise_for_status()

                soup = BeautifulSoup(resp.text, "html.parser")
                items = soup.select("li.poster-container")
                if not items:
                    break

                for item in items:
                    poster = item.select_one("div.film-poster")
                    if not poster:
                        continue
                    film_name = poster.get("data-film-name", "")
                    film_year_raw = poster.get("data-film-year", "")
                    slug = poster.get("data-film-slug", "")

                    rating_span = item.select_one("span.rating")
                    if not rating_span:
                        continue
                    rating_val = _parse_rated_class(rating_span.get("class", []))
                    if rating_val is None:
                        continue

                    ratings.append(LetterboxdRating(
                        title=film_name,
                        year=int(film_year_raw) if film_year_raw.isdigit() else None,
                        rating=rating_val,
                        slug=slug,
                    ))

                # Next page?
                if not soup.select_one("a.next"):
                    break
                page += 1

        return ratings

    def build_index(
        self, ratings: list[LetterboxdRating]
    ) -> dict[tuple[str, Optional[int]], float]:
        """Return a lookup dict keyed by (normalized_title, year) → rating."""
        return {
            (_normalize(r.title), r.year): r.rating
            for r in ratings
        }
