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
import unicodedata
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


def _ascii_title(title: str) -> str:
    """Strip diacritics: 'Le Samouraï' → 'Le Samourai'. Blu-ray.com search
    doesn't handle non-ASCII characters reliably."""
    return "".join(
        c for c in unicodedata.normalize("NFD", title)
        if unicodedata.category(c) != "Mn"
    )


class BlurayClient:

    def __init__(self):
        self.base = settings.bluray_base_url

    async def search(self, title: str, year: int | None = None,
                     fmt: str | None = None) -> list[dict]:
        """Search blu-ray.com for a movie title. Returns list of release stubs."""
        params = {
            "keyword": title,
            "submit": "Search",
            "action": "search",
        }
        if year:
            params["yearfrom"] = str(year)
            params["yearto"]   = str(year)
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
        # Pass raw bytes so BeautifulSoup reads the page's declared charset.
        # httpx decodes as UTF-8 by default, turning Latin-1 bytes like 0xB3 (³)
        # into U+FFFD replacement characters. Passing bytes lets bs4 detect the
        # correct encoding from the <meta charset> tag.
        return _parse_release_page(bluray_com_id, r.content)

    async def search_by_upc(self, upc: str) -> dict | None:
        """Search Blu-ray.com by UPC/barcode. Handles UPC-A (12-digit) and EAN-13.

        Tries three strategies in order:
          1. Main search with full barcode (works for most UPC-A codes)
          2. Main search with leading digit stripped (EAN-13 → UPC-A fallback)
          3. Quicksearch endpoint (autocomplete API — finds international EAN-13
             codes that the main search misses, e.g. UK Arrow/Eureka releases)
        """
        result = await self._fetch_by_barcode(upc)
        if result:
            return result

        # EAN-13 fallback: strip the leading digit to get UPC-A (12 digits).
        if len(upc) == 13 and upc.isdigit():
            result = await self._fetch_by_barcode(upc[1:])
            if result:
                return result

        # Quicksearch fallback: Blu-ray.com's autocomplete endpoint finds
        # international EAN-13 barcodes that the main keyword search misses.
        return await self._quicksearch_by_barcode(upc)

    async def _quicksearch_by_barcode(self, barcode: str) -> dict | None:
        """Use Blu-ray.com's quicksearch (autocomplete) endpoint for barcode lookup.

        Blu-ray.com redirects to the exact release page on an EAN match, so we
        detect the redirect and parse the page directly rather than treating it
        as a search-results list (which would pick up 'Similar titles' hoverlinks).
        """
        qs_headers = {
            **HEADERS,
            "Referer": "https://www.blu-ray.com/",
            "X-Requested-With": "XMLHttpRequest",
        }
        params = {
            "quicksearch": "1",
            "quicksearch_keyword": barcode,
            "section": "bluraymovies",
            "quicksearch_country": "ALL",
        }
        async with httpx.AsyncClient(headers=qs_headers, timeout=20, follow_redirects=True) as client:
            r = await client.get(f"{self.base}/search/", params=params)
            if r.status_code != 200:
                return None
            final_url = str(r.url)

        # Redirect to a release detail page — parse it directly.
        id_match = re.search(r'/movies/[^/]+/(\d+)/?$', final_url)
        if id_match and "search" not in final_url:
            bluray_id = int(id_match.group(1))
            return _parse_release_page(bluray_id, r.content)

        return None

    async def _fetch_by_barcode(self, barcode: str) -> dict | None:
        """Search Blu-ray.com for a single barcode string.

        Handles two Blu-ray.com behaviours:
          a) Returns a search-results HTML page  → parse hoverlinks
          b) Redirects straight to a release page → parse as release detail
        """
        params = {
            "keyword": barcode,
            "submit": "Search",
            "action": "search",
        }
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            r = await client.get(f"{self.base}/movies/search.php", params=params)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            final_url = str(r.url)

        # Case (b): Blu-ray.com redirected to a release detail page directly.
        # The URL changes from search.php to /movies/<slug>/<id>/
        id_match = re.search(r'/movies/[^/]+/(\d+)/?$', final_url)
        if id_match and "search.php" not in final_url:
            bluray_id = int(id_match.group(1))
            return _parse_release_page(bluray_id, r.content)

        # Case (a): normal search-results page.
        candidates = _parse_search_results(r.text)
        if not candidates:
            return None
        stub = candidates[0]
        # Always pass the detail_url from the stub — the fallback URL uses a "_"
        # placeholder that Blu-ray.com returns 404 for on many releases.
        details = await self.get_release(stub["bluray_com_id"], stub.get("detail_url"))
        if details:
            # film_year from the detail page <title> is most reliable;
            # fall back to year in stub title e.g. "Night of the Living Dead 4K (1968)"
            if not details.get("film_year"):
                year = stub.get("year")
                if not year:
                    m = re.search(r'\((\d{4})\)', stub.get("title", ""))
                    if m:
                        year = int(m.group(1))
                if year:
                    details["film_year"] = year
        return details

    async def search_and_get_best(self, title: str, year: int | None = None,
                                   label: str | None = None,
                                   fmt: str | None = None) -> dict | None:
        """Search and return the best-matching release details."""
        candidates = await self.search(title, year, fmt)
        if not candidates:
            candidates = await self.search(title, fmt=fmt)
        # Retry with diacritics stripped (handles titles like "Le Samouraï")
        if not candidates:
            ascii = _ascii_title(title)
            if ascii != title:
                candidates = await self.search(ascii, year, fmt)
            if not candidates and ascii != title:
                candidates = await self.search(ascii, fmt=fmt)
        # Final fallback: drop format filter so a disc in the wrong category still resolves
        if not candidates and fmt:
            candidates = await self.search(title, year)
            if not candidates:
                candidates = await self.search(title)
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


def _parse_release_page(bluray_com_id: int, html: str | bytes) -> dict:
    """Parse a blu-ray.com movie details page into structured release metadata."""
    soup = BeautifulSoup(html, "html.parser")
    data: dict[str, Any] = {"bluray_com_id": bluray_com_id}

    # Title — h1 inside the main content area
    title_el = soup.select_one("h1")
    raw_title = title_el.get_text(strip=True) if title_el else ""
    # Strip trailing format branding appended by Blu-ray.com to the h1
    # e.g. "...Produced by Val Lewton Blu-ray" → "...Produced by Val Lewton"
    data["title"] = re.sub(
        r"\s*\b(4K Ultra HD|Blu-ray|Blu ray|Bluray|UHD|DVD)\b\s*$",
        "", raw_title, flags=re.IGNORECASE,
    ).strip()

    # Film year — Blu-ray.com page <title> is typically:
    # "Night of the Living Dead 4K Blu-ray 1968 | Blu-ray Authority"
    # Extract the last standalone 4-digit year before the pipe/separator.
    film_year: int | None = None
    page_title_el = soup.find("title")
    if page_title_el:
        page_title_text = page_title_el.get_text()
        # Take text before any "|" separator (the movie portion)
        movie_portion = page_title_text.split("|")[0]
        year_matches = re.findall(r"\b(19|20)\d{2}\b", movie_portion)
        if year_matches:
            film_year = int(year_matches[-1])  # last year = film year (not format year)
    # Year fallback for international release pages whose <title> has no year
    # (e.g. "Tenebrae 4K Blu-ray (Standard Edition) (United Kingdom)").
    # Blu-ray.com links year-browser pages as /year/YYYY/ — grab first valid hit.
    if not film_year:
        for link in soup.find_all("a", href=re.compile(r"/year/\d{4}/")):
            m = re.search(r"/year/(\d{4})/", link.get("href", ""))
            if m:
                y = int(m.group(1))
                if 1900 <= y <= 2030:
                    film_year = y
                    break
    data["film_year"] = film_year

    # Release country — flag <img src=".../flags/US.png" id="countryflag">
    # The title/alt attributes may be absent; extract the 2-letter code from the filename.
    flag_img = soup.find("img", src=re.compile(r"static-bluray\.com/flags/"))
    if flag_img:
        flag_src = flag_img.get("src", "")
        country = flag_img.get("title", "") or flag_img.get("alt", "")
        if not country:
            m = re.search(r"/flags/([A-Za-z]+)\.png", flag_src)
            country = m.group(1).upper() if m else ""
        data["region"] = country
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

            # Disc count: "Single disc", "Two-disc set", "3-disc", "1 disc", etc.
            count_m = re.search(
                r"(single|one|two|three|four|five|six|\d+)[- ]disc",
                section, re.IGNORECASE
            )
            if count_m:
                word_map = {"single": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
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

    # Films included (box sets / multi-film discs)
    # subheadingtitle can contain:
    #   A) slash-separated film titles: "Film A / Film B / Film C | Edition info"
    #   B) bonus-film descriptor: includes "Murder a la Mod" on BD / 4K Ultra HD + Blu-ray
    #   C) single alternate/original title or edition note: Straume, 75th Anniversary
    #      Case C should NOT be treated as films_included

    FORMAT_RE = re.compile(
        r"\b(4K|UHD|Ultra\s*HD|Blu[- ]?ray|BD|Digital\s*HD|DVD|HDR|SDR|HEVC)\b",
        re.IGNORECASE,
    )

    def _extract_included_film(part):
        # Quoted form: includes "X" on BD  (ASCII or Unicode curly quotes)
        m = re.search(
            u'includes?\\s+[\\u201c\\u2018\\u00ab\\"\\\'](.*?)[\\u201d\\u2019\\u00bb\\"\\\']',
            part, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()
        # Unquoted form: includes X on BD / on Blu-ray
        m = re.search(
            r"includes?\s+(.+?)\s+on\s+\b(BD|Blu[- ]?ray|UHD|Disc)",
            part, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()
        return None

    films_included = []
    subtitle_el = soup.select_one("span.subheadingtitle")
    if subtitle_el:
        raw = subtitle_el.get_text(strip=True)
        raw = raw.split("|")[0]
        parts = [p.strip() for p in raw.split("/")]

        plain_titles = []
        bonus_titles = []

        for part in parts:
            if not part or len(part) <= 3:
                continue
            if re.match(r"^\d[\d,]*\s*copies?$", part, re.IGNORECASE):
                continue
            if FORMAT_RE.search(part) and len(part) < 40:
                continue
            extracted = _extract_included_film(part)
            if extracted:
                bonus_titles.append(extracted)
            else:
                plain_titles.append(part)

        if bonus_titles:
            # Bonus film via "includes X" — prepend cleaned disc title as film 1
            main_title = re.sub(
                r"\s*\b(4K|UHD|Ultra\s*HD|Blu[- ]?ray|BD)\b.*$",
                "", data.get("title", ""), flags=re.IGNORECASE,
            ).strip()
            if main_title:
                films_included.append(main_title)
            films_included.extend(bonus_titles)
        elif len(plain_titles) >= 2:
            # Multiple slash-separated titles = genuine box set listing
            films_included = plain_titles
        # else: single plain item (alt title, edition note) — leave empty

    data["films_included"] = films_included

    # Filter non-film items: digital copy descriptors, edition labels, packaging notes.
    # "Arrow Video Exclusive / Limited Edition" in subheadingtitle must not become
    # fake film titles that send step 3.6 to TMDB for "Limited Edition" (Bernard Rapp, 1997).
    _NON_FILM_RE = re.compile(
        r'^\s*('
        # Digital copy / streaming tokens
        r'Digi(Book|Pack|Pak|tal(\s*(Copy|HD|MA|Redemption))?)|UltraViolet|'
        r'UV(\s+Digital)?\s*Copy|Bonus\s*Disc|Movies\s*Anywhere|iTunes|Vudu|'
        r'Digital\s*(Copy|Download|HD|MA)|'
        # Edition / version descriptors
        r'(?:Limited|Special|Collector[\'s]*|Deluxe|Standard|Premium|Anniversary|'
        r'Director[\'s]*|Ultimate|Numbered|Theatrical|Restored?|Definitive|'
        r'Remastered|Criterion|Exclusive)\s*(?:Edition|Version|Cut|Set|Box\s*Set)?|'
        # Label-branded exclusives ("Arrow Video Exclusive", "Criterion Exclusive")
        r'\S+\s+(?:Video\s+)?Exclusive'
        r')\s*$',
        re.IGNORECASE,
    )
    data["films_included"] = [
        f for f in films_included
        if not _NON_FILM_RE.match(f.get("title") if isinstance(f, dict) else f)
    ]

    # IMDb ID — Blu-ray.com detail pages link to IMDb for the film.
    # This gives us an unambiguous key for TMDB when title alone is ambiguous
    # (e.g. "Tenebrae" 1982 Argento vs "Tenebrae" 2018).
    data["imdb_id"] = ""
    for a in soup.find_all("a", href=re.compile(r"imdb\.com/title/tt\d+")):
        m = re.search(r"(tt\d+)", a.get("href", ""))
        if m:
            data["imdb_id"] = m.group(1)
            break

    # Fallback: bundle-film links and director from div#movie_info.
    # Box sets like "The Before Trilogy" list films as hoverlink anchors inside
    # div#movie_info (title="Before Sunrise (1995)") rather than in subheadingtitle.
    # Restrict search to div#movie_info — the "Similar titles you might also like"
    # section outside that div uses the same hoverlink class and must be excluded.
    _TITLE_YEAR_RE = re.compile(r'^(.+?)\s*\((\d{4})\)\s*$')  # groups: title, year
    _FORMAT_SUFFIX_RE = re.compile(
        r'\s*\b(4K|UHD|Ultra\s*HD|Blu[- ]?ray|BD)\b.*$', re.IGNORECASE
    )

    data["director"] = ""
    movie_info_div = soup.find("div", id="movie_info")
    if movie_info_div:
        # Handle both "Director:" and "Directors:" (multi-director box sets)
        info_text = movie_info_div.get_text(" ", strip=True)
        d_m = re.search(r'Directors?:\s*(.+?)(?=\s+\w+:|$)', info_text)
        if d_m:
            director = d_m.group(1).strip()
            # Normalize comma-separated names to " / " (consistent with TMDB enrichment)
            director = re.sub(r',\s*', ' / ', director)
            data["director"] = director

        # Bundle films from div#movie_info hoverlinks — only if subheadingtitle
        # parsing found nothing (e.g. "The Before Trilogy" lists films this way).
        if not data["films_included"]:
            bundle = []
            for link in movie_info_div.select("a.hoverlink[data-productid]"):
                t = link.get("title", "")
                m = _TITLE_YEAR_RE.match(t)
                if m:
                    film_title = _FORMAT_SUFFIX_RE.sub("", m.group(1)).strip()
                    if film_title and not any(
                        (b.get("title") if isinstance(b, dict) else b) == film_title
                        for b in bundle
                    ):
                        bundle.append({"title": film_title, "year_hint": int(m.group(2))})
            if len(bundle) >= 2:
                data["films_included"] = bundle

    # Third fallback: "This Blu-ray bundle includes" section.
    # Some box sets (e.g. TMNT Trilogy 4K) list individual film thumbnails in a
    # separate bundle section that may sit outside div#movie_info.
    if not data["films_included"]:
        bundle = []
        similar_titles_el = soup.find(string=re.compile(r"similar titles", re.I))
        for bundle_text in soup.find_all(string=re.compile(r"bundle\s+includes?", re.I)):
            container = bundle_text.parent
            for _ in range(6):
                if container is None:
                    break
                # Exclude anything inside the "Similar titles" section
                if similar_titles_el and similar_titles_el in container.descendants:
                    container = container.parent
                    continue
                links = container.select("a.hoverlink[data-productid]")
                if not links:
                    container = container.parent
                    continue
                for link in links:
                    t = link.get("title", "")
                    m = _TITLE_YEAR_RE.match(t)
                    if m:
                        film_title = _FORMAT_SUFFIX_RE.sub("", m.group(1)).strip()
                        if film_title and not any(
                            (b.get("title") if isinstance(b, dict) else b) == film_title
                            for b in bundle
                        ):
                            bundle.append({"title": film_title, "year_hint": int(m.group(2))})
                break
            if len(bundle) >= 2:
                break
        if len(bundle) >= 2:
            data["films_included"] = bundle

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
