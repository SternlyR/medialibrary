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

    async def get_release(self, bluray_com_id: int, url: str | None = None) -> dict | None:
        """Fetch full release details page for a given blu-ray.com movie ID."""
        if not url:
            url = f"{self.base}/movies/_/{bluray_com_id}/"
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

        details = await self.get_release(best["bluray_com_id"], best.get("detail_url"))
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

            # Capture full detail page URL (e.g. /movies/Airplane-4K-Blu-ray/370108/)
            href = link.get("href", "")
            detail_url = ("https://www.blu-ray.com" + href) if href.startswith("/") else href

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
                "detail_url": detail_url,
            })
        except Exception:
            continue

    return results


def _parse_release_page(bluray_com_id: int, html: str) -> dict:
    """Parse a blu-ray.com movie details page into structured release metadata."""
    soup = BeautifulSoup(html, "html.parser")
    data: dict[str, Any] = {"bluray_com_id": bluray_com_id}

    # Title — h1 inside the main content area
    title_el = soup.select_one("h1")
    data["title"] = title_el.get_text(strip=True) if title_el else ""

    # Release country — flag <img src=".../flags/UK.png" title="United Kingdom"> adjacent to h1
    # This is the country the disc was released in (UK, US, Germany, etc.)
    flag_img = soup.select_one("img[src*='/flags/']")
    if flag_img:
        data["region"] = flag_img.get("title", "") or flag_img.get("alt", "")
    else:
        data["region"] = ""

    # Cover art — always use the full-res _front.jpg from the CDN (the page serves _medium.jpg)
    data["cover_url"] = f"{COVER_BASE}/{bluray_com_id}_front.jpg"

    # Back cover — look in script tags for the _back.jpg reference
    back_url = ""
    for script in soup.find_all("script"):
        script_text = script.string or ""
        m = re.search(rf"{bluray_com_id}_back\.jpg", script_text)
        if m:
            back_url = f"{COVER_BASE}/{bluray_com_id}_back.jpg"
            break
    data["cover_url_back"] = back_url

    # Info line: <span class="subheading grey"> contains label | year | runtime | rating | release date
    info_span = soup.select_one("span.subheading.grey, span.subheading[class*='grey']")
    data["label"] = ""
    data["physical_release_date"] = ""
    data["runtime_minutes"] = None
    if info_span:
        # First <a> tag is the studio/label
        links = info_span.find_all("a")
        if links:
            data["label"] = links[0].get_text(strip=True)

        # MPAA rating — "Rated XX" text node
        info_text = info_span.get_text(" ", strip=True)
        rated_m = re.search(r"Rated\s+(\S+)", info_text)
        if rated_m:
            data["mpaa_rating"] = rated_m.group(1)

        # Release date — last <a> whose text looks like a date (e.g. "Dec 09, 2025")
        date_pattern = re.compile(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b",
            re.IGNORECASE,
        )
        for a in reversed(links):
            if date_pattern.search(a.get_text(strip=True)):
                data["physical_release_date"] = a.get_text(strip=True)
                break

        # Runtime from <span id="runtime">
        runtime_span = info_span.select_one("span#runtime, span[id='runtime']")
        if runtime_span:
            data["runtime_minutes"] = _parse_runtime(runtime_span.get_text(strip=True))

    # Parse <span class="subheading"> sections (Video, Discs, Playback, etc.)
    # Each section is a <span class="subheading"> followed by <br>-separated lines
    data["aspect_ratio"] = ""
    data["format"] = ""
    data["disc_count"] = None
    data["edition"] = ""

    for heading in soup.select("span.subheading"):
        # Skip the grey info-line span
        if "grey" in (heading.get("class") or []):
            continue
        heading_text = heading.get_text(strip=True).lower()

        # Collect text lines that follow this heading (siblings until next block element)
        lines = []
        for sib in heading.next_siblings:
            if hasattr(sib, "name"):
                if sib.name in ("br",):
                    continue
                if sib.name in ("span", "div", "table", "h1", "h2", "h3"):
                    break
                lines.append(sib.get_text(" ", strip=True))
            else:
                txt = str(sib).strip()
                if txt:
                    lines.append(txt)

        section = " ".join(lines)

        if "video" in heading_text:
            # Aspect ratio
            ar_m = re.search(r"[Aa]spect ratio[:\s]+([\d.]+:\d+)", section)
            if not ar_m:
                ar_m = re.search(r"([\d.]+:[\d.]+)", section)
            if ar_m:
                data["aspect_ratio"] = ar_m.group(1)

        elif "disc" in heading_text:
            # Format: look for "4K Ultra HD" or "Blu-ray" or "DVD"
            if re.search(r"4K|Ultra HD|UHD", section, re.IGNORECASE):
                data["format"] = "UHD"
            elif re.search(r"Blu-ray|Blu ray|Bluray", section, re.IGNORECASE):
                data["format"] = "Blu-Ray"
            elif re.search(r"DVD", section, re.IGNORECASE):
                data["format"] = "DVD"

            # Disc count: "Two-disc set", "3-disc", "1 disc", etc.
            count_m = re.search(
                r"(one|two|three|four|five|six|\d+)[- ]disc",
                section, re.IGNORECASE
            )
            if count_m:
                word_map = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
                val = count_m.group(1).lower()
                data["disc_count"] = word_map.get(val, None) or int(val)

        elif "playback" in heading_text:
            # Only use Playback section for region if the flag img didn't give us a country
            if not data["region"]:
                region_m = re.search(r"[Rr]egion\s+(\w+)", section)
                if region_m:
                    data["region"] = _normalize_region(region_m.group(1))

        elif "edition" in heading_text or "version" in heading_text:
            data["edition"] = section.strip()

    # Fallback: try specs table (older page layouts)
    if not data["label"] or not data["physical_release_date"]:
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

        data["label"] = data["label"] or spec("studio", "label", "distributor", "publisher")
        data["physical_release_date"] = data["physical_release_date"] or spec("release date", "street date")
        data["region"] = data["region"] or _normalize_region(spec("region", "blu-ray region", "region code"))
        data["format"] = data["format"] or _normalize_format(spec("format", "disc format"))
        data["edition"] = data["edition"] or spec("edition", "version")
        data["aspect_ratio"] = data["aspect_ratio"] or spec("aspect ratio", "ratio")
        if not data["runtime_minutes"]:
            data["runtime_minutes"] = _parse_runtime(spec("run time", "running time", "runtime"))

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
