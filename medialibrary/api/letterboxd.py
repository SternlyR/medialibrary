"""Letterboxd ratings scraper.

Strategy (two-tier):
1. RSS feed (/<username>/rss/) — not Cloudflare-blocked, contains recent
   diary entries with ratings, film titles, years, and TMDB IDs.
2. User film page (/<username>/film/<slug>/) — also not Cloudflare-blocked,
   covers older ratings not present in the RSS feed.  The slug is constructed
   from the film title; a year-suffixed variant is tried on 404.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

import httpx
from bs4 import BeautifulSoup

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


def _title_to_slug(title: str) -> str:
    """Best-effort conversion of a film title to a Letterboxd slug.

    e.g. "Past Lives" -> "past-lives", "Schindler's List" -> "schindlers-list"
    """
    slug = title.lower()
    slug = re.sub(r"['\u2019\u2018`]", "", slug)   # remove apostrophes
    slug = re.sub(r"[^a-z0-9\s-]", " ", slug)       # non-alnum -> space
    slug = re.sub(r"\s+", "-", slug.strip())          # spaces -> hyphens
    slug = re.sub(r"-{2,}", "-", slug).strip("-")     # collapse hyphens
    return slug


def _parse_stars(text: str) -> Optional[float]:
    """Convert a star string like '★★★★½' to a float (4.5)."""
    text = text.strip()
    if not text:
        return None
    full = text.count("★")
    half = 0.5 if "½" in text else 0.0
    return float(full) + half if (full or half) else None


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
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async def get_rating_for_film(
        self,
        username: str,
        title: str,
        year: Optional[int] = None,
        tmdb_id: Optional[int] = None,
    ) -> Optional[float]:
        """Return the user's star rating (0.5-5.0) for a film, or None.

        Tries the RSS feed first (fast, recent films), then falls back to
        fetching the user's film page directly (covers older ratings).
        """
        # ── Tier 1: RSS feed ─────────────────────────────────────────────────
        ratings = await self.get_all_ratings(username)

        if tmdb_id:
            for r in ratings:
                if r.tmdb_id == tmdb_id:
                    return r.rating

        norm = _normalize(title)
        for r in ratings:
            if _normalize(r.title) == norm:
                if year is None or r.year is None or abs(r.year - year) <= 1:
                    return r.rating

        # ── Tier 2: direct user film page (older ratings) ────────────────────
        slug = _title_to_slug(title)
        rating = await self._scrape_user_film_page(username, slug)
        if rating is not None:
            return rating

        # Try slug with year suffix for disambiguation (e.g. "alien-1979")
        if year:
            rating = await self._scrape_user_film_page(username, f"{slug}-{year}")

        return rating

    async def _scrape_user_film_page(
        self, username: str, slug: str
    ) -> Optional[float]:
        """Fetch the user's diary/review page and extract their most recent rating.

        Tries /{username}/film/{slug}/diary/ first — covers all logged viewings
        including star-only logs with no written review, and the first row is
        always the most recent entry.

        Falls back to /reviews/ then the base film page.

        Rating is in <div class="rateit-range" aria-valuenow="N"> on a 0–10
        scale (10 = 5 stars, 9 = 4.5 stars, … 1 = 0.5 stars, 0 = no rating).
        """
        for path in ["diary/", "reviews/", ""]:
            url = f"{self.BASE}/{username}/film/{slug}/{path}"
            async with httpx.AsyncClient(
                headers=self._HEADERS, follow_redirects=True, timeout=10
            ) as client:
                try:
                    resp = await client.get(url)
                    if resp.status_code in (404, 403):
                        continue
                    resp.raise_for_status()
                except httpx.HTTPError:
                    continue

            soup = BeautifulSoup(resp.text, "html.parser")
            content = soup.select_one("div#content") or soup

            # Primary: rateit widget — first match is the most recent entry
            rateit = content.select_one("div.rateit-range[aria-valuenow]")
            if rateit:
                try:
                    val = int(rateit["aria-valuenow"])
                    if val > 0:
                        return val / 2.0
                except (ValueError, KeyError):
                    pass

            # Fallback: SVG star glyph (older page format / base film page)
            svg = content.select_one("svg.glyph.-rating")
            if svg:
                label = svg.get("aria-label", "")
                rating = _parse_stars(label)
                if rating is not None:
                    return rating
                title_el = svg.find("title")
                if title_el:
                    rating = _parse_stars(title_el.get_text())
                    if rating is not None:
                        return rating

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
