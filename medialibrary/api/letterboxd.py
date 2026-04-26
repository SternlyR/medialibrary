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

# curl_cffi impersonates Chrome's TLS fingerprint, bypassing Cloudflare bot
# detection that blocks plain httpx/requests on user sub-pages.
# Install with: pip install curl_cffi
try:
    from curl_cffi.requests import AsyncSession as _CurlSession
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False

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

    async def _get(self, url: str, timeout: int = 10) -> tuple[int, str]:
        """Fetch a URL and return (status_code, response_text).

        Uses curl_cffi with Chrome impersonation if available (bypasses
        Cloudflare TLS fingerprint detection).  Falls back to httpx.
        """
        if _HAS_CURL_CFFI:
            try:
                async with _CurlSession(impersonate="chrome120") as session:
                    r = await session.get(
                        url, headers=self._HEADERS,
                        allow_redirects=True, timeout=timeout,
                    )
                    return r.status_code, r.text
            except Exception:
                return 0, ""
        else:
            async with httpx.AsyncClient(
                headers=self._HEADERS, follow_redirects=True, timeout=timeout
            ) as client:
                try:
                    r = await client.get(url)
                    return r.status_code, r.text
                except httpx.HTTPError:
                    return 0, ""

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

    def _parse_rating_from_page(self, soup: "BeautifulSoup") -> Optional[float]:
        """Extract a star rating from any Letterboxd page soup.

        Tries sources in order of reliability:
        1. twitter:data2 meta tag — server-rendered on individual viewing pages
        2. input.rateit-field — server-rendered backing field (diary page)
        3. div.rateit-range aria-valuenow — may be JS-set
        4. span.rating text — reviews listing page
        5. svg.glyph.-rating aria-label — base film page
        """
        # 1. Twitter meta (individual viewing pages, e.g. /seancohen/film/thief/2/)
        meta = soup.find("meta", attrs={"name": "twitter:data2"})
        if meta:
            r = _parse_stars(meta.get("content", ""))
            if r is not None:
                return r

        content = soup.select_one("div#content") or soup

        # 2. Hidden range input (diary page — server-rendered)
        ri = content.select_one("input.rateit-field[type='range']")
        if ri:
            try:
                val = int(ri.get("value", "0"))
                if val > 0:
                    return val / 2.0
            except (ValueError, TypeError):
                pass

        # 3. Rateit div aria-valuenow (may be JS-set)
        rd = content.select_one("div.rateit-range")
        if rd:
            try:
                val = int(rd.get("aria-valuenow", "0"))
                if val > 0:
                    return val / 2.0
            except (ValueError, TypeError):
                pass

        # 4. Star text in span.rating (reviews page)
        span = content.select_one("span.rating")
        if span:
            r = _parse_stars(span.get_text(strip=True))
            if r is not None:
                return r

        # 5. SVG glyph (base film page — shows oldest entry)
        svg = content.select_one("svg.glyph.-rating")
        if svg:
            r = _parse_stars(svg.get("aria-label", ""))
            if r is not None:
                return r
            title_el = svg.find("title")
            if title_el:
                r = _parse_stars(title_el.get_text())
                if r is not None:
                    return r

        return None

    async def _probe_numbered_reviews(
        self, username: str, slug: str, max_n: int = 5
    ) -> Optional[float]:
        """Probe /slug/1/, /slug/2/, … ascending until 404, return last found rating."""
        last_rating = None
        for n in range(1, max_n + 1):
            url = f"{self.BASE}/{username}/film/{slug}/{n}/"
            status, html = await self._get(url)
            if status == 404:
                break
            if status != 200 or not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            r = self._parse_rating_from_page(soup)
            if r is not None:
                last_rating = r
        return last_rating

    async def _scrape_user_film_page(
        self, username: str, slug: str
    ) -> Optional[float]:
        """Fetch the user's diary/review page and extract their most recent rating.

        Tries in order:
        1. /{username}/film/{slug}/diary/  — full diary listing
        2. /{username}/film/{slug}/reviews/ — review listing
        3. /{username}/film/{slug}/         — base film page (shows OLDEST entry).
           After parsing, probes numbered review pages (/1/, /2/, …) to find
           the most recent rating.
        """
        for path in ["diary/", "reviews/", "activity/", ""]:
            url = f"{self.BASE}/{username}/film/{slug}/{path}"
            status, html = await self._get(url)
            if status in (403, 404) or not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            rating = self._parse_rating_from_page(soup)

            if path == "":
                recent = await self._probe_numbered_reviews(username, slug)
                return recent if recent is not None else rating

            if rating is not None:
                return rating

        return None

    async def get_all_ratings(self, username: str) -> list[LetterboxdRating]:
        """Fetch and parse the user's RSS feed into a list of rated films."""
        url = f"{self.BASE}/{username}/rss/"
        status, text = await self._get(url, timeout=15)
        if status != 200 or not text:
            return []

        try:
            root = ET.fromstring(text)
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
