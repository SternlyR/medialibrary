"""Blu-ray.com scraper for physical release metadata.

Blu-ray.com is the authoritative source for physical disc data:
  - Physical release date
  - Label / distributor
  - Region (A, B, C or US, UK, etc.)
  - Edition / variant name
  - Disc count, aspect ratio
  - Cover art (front + back)
  - UPC codes

This module provides two entry points:
  1. search(title, year) → list of candidate releases
  2. get_release(bluray_com_id) → full release details

Note: Blu-ray.com does not have a public API. This scraper uses their
HTML search results. Be respectful of rate limits — add delays between
requests when doing bulk backfills.
"""

from __future__ import annotations

import re
import httpx
from bs4 import BeautifulSoup
from typing import Any

from medialibrary.config import settings

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

COVER_BASE = "https://images.static-bluray.com/movies/covers"

VIDEO_RESOLUTION_IDS = {
    "UHD": 2683,
    "Blu-Ray": 278,
    "DVD": 2365,
}


class BlurayClient:

    def __init__(self):
        self.base = settings.bluray_base_url

    async def search(self, title: str, year: int | None = None,
                     fmt: str | None = None) -> list[dict]:
        """Search blu-ray.com for a movie title. Returns list of release stubs."""
        params = {
            "keyword": title.replace(" ", "+"),
            "submit": "Search",
            "action": "search",
        }
        if year:
            params["yearfrom"] = str(year)
        if fmt and fmt in VIDEO_RESOLUTION_IDS:
            params["videoresolutionid"] = str(VIDEO_RESOLUTION_IDS[fmt])
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            r = await client.get(f"{self.base}/movies/search.php", params=params)
            r.raise_for_status()
        return _parse_search_results(r.text)

    async def get_release(self, bluray_com_id: int) -> dict | None:
        """Fetch full release details page for a given blu-ray.com movie ID."""
        url = f"{self.base}/movies/details.aspx?id={bluray_com_id}"
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code == 404:
                return None
            r.raise_for_status()
        return _parse_release_page(bluray_com_id, r.text)

    async def search_and_get_best(self, title: str, year: int | None = None,
                                   label: str | None = None,
                                   fmt: str | None = None) -> dict | None:
        """Search and return the best-matching release details."""
        candidates = await self.search(title, year, fmt)
        if not candidates:
            # Retry without year filter if no results
            candidates = await self.search(title, fmt=fmt)
        if not candidates:
            return None

        # Filter by label if provided
        if label:
            label_lower = label.lower()
            filtered = [c for c in candidates if label_lower in c.get("label", "").lower()]
            if filtered:
                candidates = filtered

        best = candidates[0]
        if not best.get("bluray_com_id"):
            return best

        details = await self.get_release(best["bluray_com_id"])
        return details or best


def _parse_search_results(html: str) -> list[dict]:
    """Parse blu-ray.com search result page into a list of release stubs."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for link in soup.select("a.hoverlink[data-productid]"):
        try:
            product_id = link.get("data-productid", "")
            if not product_id:
                continue
            bluray_id = int(product_id)
            title = link.get_text(strip=True) or link.get("title", "")
            cover_url = f"{COVER_BASE}/{bluray_id}_front.jpg"

            # Grab surrounding container for year/label
            container = link.find_parent("div") or link.find_parent("td") or link
            text = container.get_text(" ", strip=True)
            year_match = re.search(r"\b(19|20)\d{2}\b", text)
            year = int(year_match.group()) if year_match else None

            results.append({
                "bluray_com_id": bluray_id,
                "title": title,
                "year": year,
                "label": "",
                "cover_url": cover_url,
            })
        except Exception:
            continue

    return results


def _parse_release_page(bluray_com_id: int, html: str) -> dict:
    """Parse a blu-ray.com movie details page into structured release metadata."""
    soup = BeautifulSoup(html, "html.parser")
    data: dict[str, Any] = {"bluray_com_id": bluray_com_id}

    # Title
    title_el = soup.select_one("h1, .page_title, #ctl00_ContentPlaceHolder1_lblTitle")
    data["title"] = title_el.get_text(strip=True) if title_el else ""

    # Cover art — front cover image
    cover_img = soup.select_one(
        "img.coverart, img[src*='covers'][src*='front'], "
        "#ctl00_ContentPlaceHolder1_imgCover"
    )
    if cover_img:
        src = cover_img.get("src", "")
        data["cover_url"] = src if src.startswith("http") else settings.bluray_base_url + src

    # Details table — most metadata lives in a key/value table
    specs: dict[str, str] = {}
    for row in soup.select("table.specs tr, div.specs .row, #specifications tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            key = cells[0].get_text(strip=True).rstrip(":").lower()
            val = cells[1].get_text(" ", strip=True)
            specs[key] = val

    def spec(*keys: str) -> str:
        for k in keys:
            if k in specs:
                return specs[k]
        return ""

    data["physical_release_date"] = spec("release date", "street date", "release")
    data["label"] = spec("studio", "label", "distributor", "publisher")
    data["region"] = spec("region", "blu-ray region", "region code")
    data["format"] = spec("format", "disc format")
    data["edition"] = spec("edition", "version")
    data["disc_count"] = spec("discs", "number of discs", "disc count")
    data["aspect_ratio"] = spec("aspect ratio", "video", "ratio")
    data["upc"] = spec("upc", "barcode", "upc/ean")
    data["runtime_minutes"] = _parse_runtime(spec("run time", "running time", "runtime"))

    # Region normalization
    data["region"] = _normalize_region(data["region"])

    # Format normalization
    data["format"] = _normalize_format(data["format"])

    # Disc count as int
    try:
        data["disc_count"] = int(re.search(r"\d+", data["disc_count"]).group()) if data["disc_count"] else None
    except Exception:
        data["disc_count"] = None

    return data


def _parse_runtime(runtime_str: str) -> int | None:
    if not runtime_str:
        return None
    # Handles: "143 minutes", "2:23:00", "143 min"
    m = re.search(r"(\d+)\s*(?:min|minutes?)", runtime_str, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # HH:MM:SS
    m = re.match(r"(\d+):(\d{2})(?::\d{2})?", runtime_str)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return None


def _normalize_region(region: str) -> str:
    r = region.upper()
    if not r:
        return ""
    if any(x in r for x in ["FREE", "ALL", "0"]):
        return "Free"
    if "A" in r and "B" not in r and "C" not in r:
        return "A"
    if "B" in r and "A" not in r and "C" not in r:
        return "B"
    if "C" in r and "A" not in r and "B" not in r:
        return "C"
    if "US" in r or "UNITED STATES" in r or "USA" in r:
        return "US"
    if "UK" in r or "UNITED KINGDOM" in r or "BRITAIN" in r:
        return "UK"
    return region


def _normalize_format(fmt: str) -> str:
    f = fmt.upper()
    if "4K" in f or "UHD" in f:
        return "UHD"
    if "BLU" in f:
        return "Blu-Ray"
    if "DVD" in f:
        return "DVD"
    return fmt
