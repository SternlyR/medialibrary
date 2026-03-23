"""UPC / barcode lookup client.

Uses UPCitemdb (https://www.upcitemdb.com/) — free tier allows 100 lookups/day
with no API key required. Returns product info including title which is then
used to look up detailed movie metadata via TMDB.

Barcode scanning on mobile: any standard QR/barcode scanner app can read
UPC-A (12-digit) and EAN-13 (13-digit) barcodes from disc cases.
"""

from __future__ import annotations

import httpx
import re

from medialibrary.config import settings


class UPCClient:
    def __init__(self):
        self.base = settings.upcitemdb_base_url

    async def lookup(self, upc: str) -> dict | None:
        """Look up a UPC code. Returns parsed product info or None."""
        upc = upc.strip().replace("-", "").replace(" ", "")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(self.base, params={"upc": upc})
            if r.status_code == 429:
                raise RuntimeError("UPCitemdb rate limit reached (100/day on free tier). Try again tomorrow.")
            if r.status_code != 200:
                return None
            data = r.json()
            items = data.get("items", [])
            if not items:
                return None
            return self._parse(upc, items[0])

    def _parse(self, upc: str, item: dict) -> dict:
        """Extract movie-relevant fields from a UPCitemdb item."""
        title = item.get("title", "")
        description = item.get("description", "")

        # Try to extract year from description or title
        year = None
        year_match = re.search(r"\b(19|20)\d{2}\b", description + " " + title)
        if year_match:
            year = int(year_match.group())

        # Detect format from title/description
        fmt = _detect_format(title + " " + description)

        return {
            "upc": upc,
            "raw_title": title,
            "description": description,
            "brand": item.get("brand", ""),
            "format": fmt,
            "year_hint": year,
            # Cleaned title for TMDB search (strip format tags)
            "search_title": _clean_title(title),
        }


def _detect_format(text: str) -> str:
    text_upper = text.upper()
    if "4K" in text_upper or "UHD" in text_upper:
        return "UHD"
    if "BLU-RAY" in text_upper or "BLU RAY" in text_upper or "BLURAY" in text_upper:
        return "Blu-Ray"
    if "DVD" in text_upper:
        return "DVD"
    return ""


def _clean_title(title: str) -> str:
    """Strip common disc format suffixes to get a clean movie title."""
    patterns = [
        r"\[.*?\]",           # [Blu-ray], [4K UHD], [DVD], etc.
        r"\(.*?\)",           # (Blu-ray), (2023), etc.
        r"4K UHD.*$",
        r"Blu-ray.*$",
        r"DVD.*$",
        r"UHD.*$",
        r"\s*-\s*$",
    ]
    cleaned = title
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned.strip(" -,:")
