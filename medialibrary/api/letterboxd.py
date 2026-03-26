"""Letterboxd ratings via RSS feed.

Letterboxd's HTML pages are protected by Cloudflare JS challenges, so
HTML scraping is not possible from a headless HTTP client.  The per-user
RSS feed (/<username>/rss/) is publicly accessible without JS and contains
diary entries with star ratings, film titles, years, and TMDB IDs.

Limitation: the RSS feed only includes recent diary entries (roughly the
last 50 logged films).  Films logged a long time ago may not appear.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

import httpx

# XML namespace URIs used in Letterboxd RSS
_NS_LB = "https://letterboxd.com"
_NS_TMDB = "https://themoviedb.org"


def _lb(tag: str) -> str:
    return f"{{{_NS_LB}}}{tag}"


def _tmdb(tag: str) -> str:
    return f"{{{_NS_TMDB}}}{tag}"


def _normalize(title: str) -> str:
    """Lowercase, strip punctuation for fuzzy title matching."""
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def _slug_from_url(url: str) -> str:
    """Extract film slug from a Letterboxd URL."""
    m = re.search(r"/film/([^/]+)/", url or "")
    return m.group(1) if m else ""


@dataclass
class LetterboxdRating:
    title: str
    year: Optional[int]
    rating: float        # 0.5 – 5.0 in half-star increments
    slug: str
    tmdb_id: Optional[int] = field(default=None)


class LetterboxdClient:
    BASE = "https://letterboxd.com"
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml,application/xml,text/xml,*/*",
    }

    async def get_rating_for_film(
        self,
        username: str,
        title: str,
        year: Optional[int] = None,
        tmdb_id: Optional[int] = None,
    ) -> Optional[float]:
        """Return the user's star rating (0.5-5.0) for a film, or None.

        Fetches the user's RSS feed and matches by TMDB ID (preferred)
        or by normalized title + year.
        """
        ratings = await self.get_all_ratings(username)

        # Match by TMDB ID — most reliable, no title-fuzzing needed
        if tmdb_id:
            for r in ratings:
                if r.tmdb_id == tmdb_id:
                    return r.rating

        # Fall back to normalized title + year
        norm = _normalize(title)
        for r in ratings:
            if _normalize(r.title) == norm:
                if year is None or r.year is None or abs(r.year - year) <= 1:
                    return r.rating

        return None

    async def get_all_ratings(self, username: str) -> list[LetterboxdRating]:
        """Fetch and parse the user's RSS feed into a list of rated films."""
        url = f"{self.BASE}/{username}/rss/"
        async with httpx.AsyncClient(
            headers=self._HEADERS, follow_redirects=True, timeout=15
        ) as client:
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return []
            except httpx.HTTPError:
                return []

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            return []

        ratings: list[LetterboxdRating] = []
        for item in root.findall(".//item"):
            # Skip diary entries with no rating
            rating_el = item.find(_lb("memberRating"))
            if rating_el is None or not rating_el.text:
                continue
            try:
                rating_val = float(rating_el.text)
            except ValueError:
                continue

            title_el = item.find(_lb("filmTitle"))
            year_el = item.find(_lb("filmYear"))
            tmdb_el = item.find(_tmdb("movieId"))
            link_el = item.find("link")

            film_title = title_el.text if title_el is not None else ""
            film_year = (
                int(year_el.text)
                if year_el is not None and (year_el.text or "").isdigit()
                else None
            )
            film_tmdb_id = (
                int(tmdb_el.text)
                if tmdb_el is not None and (tmdb_el.text or "").isdigit()
                else None
            )
            link_text = link_el.text if link_el is not None else ""
            slug = _slug_from_url(link_text)

            ratings.append(
                LetterboxdRating(
                    title=film_title,
                    year=film_year,
                    rating=rating_val,
                    slug=slug,
                    tmdb_id=film_tmdb_id,
                )
            )

        return ratings

    def build_index(
        self, ratings: list[LetterboxdRating]
    ) -> dict[tuple[str, Optional[int]], float]:
        """Return a lookup dict keyed by (normalized_title, year) → rating."""
        return {(_normalize(r.title), r.year): r.rating for r in ratings}
