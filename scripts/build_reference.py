"""Download the published reference lists and save them as CSVs.

Run from the repo root:  python scripts/build_reference.py [source ...]

Each CSV in data/reference/ holds one source, one row per film entry:
    rank, title, year, director, country, note, url
The app imports these at startup (medialibrary/reference.py); re-run this
script only to refresh a list (e.g. a new Sight and Sound poll).
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

from bs4 import BeautifulSoup
# Wikimedia's bot filter rejects python-httpx; curl_cffi gets through.
from curl_cffi import requests as http

OUT = Path(__file__).resolve().parent.parent / "data" / "reference"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
FIELDS = ["rank", "title", "year", "director", "country", "note", "url", "tmdb_id", "imdb_id"]


def fetch(url: str) -> str:
    for attempt in range(5):
        r = http.get(url, headers={"User-Agent": UA}, allow_redirects=True, timeout=60)
        if r.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.text
    raise RuntimeError(f"{url}: still rate-limited")


def write(name: str, rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in FIELDS})
    print(f"{name}: {len(rows)} entries -> {path.relative_to(OUT.parent.parent)}")


# ── Sight and Sound 2022 (BFI) ────────────────────────────────────────────────

def _bfi_poll(url: str) -> list[dict]:
    soup = BeautifulSoup(fetch(url), "html.parser")
    rows = []
    for art in soup.select("div[class*=ResultsPage__ResultGrid] article"):
        title_el = art.find("h1")
        rank_el = art.select_one("[class*=ResultsPage__Rank]")
        if not title_el or not rank_el:
            continue
        rank = re.sub(r"\D", "", rank_el.get_text())
        year = director = country = ""
        for p in art.select("p[class*=ResultsPage__P]"):
            text = " ".join(p.get_text(" ", strip=True).split())
            if text.startswith("Directed by"):
                director = text[len("Directed by"):].strip()
            elif m := re.match(r"^(\d{4})\s*(.*)$", text):
                year, country = m.group(1), m.group(2)
        link = art.find("a")
        rows.append({
            "rank": rank,
            "title": " ".join(title_el.get_text(" ", strip=True).split()),
            "year": year,
            "director": director,
            "country": country,
            "note": "tied" if "=" in rank_el.get_text() else "",
            "url": "https://www.bfi.org.uk" + link["href"] if link and link.get("href") else url,
        })
    rows.sort(key=lambda r: int(r["rank"] or 9999))
    return rows


def sight_and_sound_critics_2022():
    rows = _bfi_poll("https://www.bfi.org.uk/sight-and-sound/greatest-films-all-time")
    resolve_tmdb_ids(rows)
    write("sight_and_sound_critics_2022", rows)


def sight_and_sound_directors_2022():
    rows = _bfi_poll("https://www.bfi.org.uk/sight-and-sound/directors-100-greatest-films-all-time")
    resolve_tmdb_ids(rows)
    write("sight_and_sound_directors_2022", rows)


# ── Award records and the National Film Registry (Wikidata) ───────────────────
# Wikipedia's rate limits block bulk reads, and Wikidata carries the same
# records as structured data, usually with TMDB ids for exact matching.

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_UA = "medialibrary-reference-builder/1.0 (personal project; github SternlyR/medialibrary)"


def _wikidata_films(qid: str, prop: str) -> list[dict]:
    """Films linked to `qid` via `prop` (P166 award received, P361 part of),
    with the year it was conferred from the P585/P580 qualifier."""
    query = f"""
    SELECT ?film ?filmLabel ?tmdb ?imdb (MIN(YEAR(?pub)) AS ?year)
           (MIN(YEAR(COALESCE(?when, ?start))) AS ?conferred)
           (GROUP_CONCAT(DISTINCT ?dirLabel; separator=", ") AS ?directors)
    WHERE {{
      ?film p:{prop} ?st . ?st ps:{prop} wd:{qid} .
      FILTER NOT EXISTS {{ ?film wdt:P31 wd:Q5 }}   # directors/actors also "receive" festival prizes
      OPTIONAL {{ ?st pq:P585 ?when }} OPTIONAL {{ ?st pq:P580 ?start }}
      OPTIONAL {{ ?film wdt:P4947 ?tmdb }} OPTIONAL {{ ?film wdt:P345 ?imdb }}
      OPTIONAL {{ ?film wdt:P577 ?pub }}
      OPTIONAL {{ ?film wdt:P57 ?dir . ?dir rdfs:label ?dirLabel FILTER(LANG(?dirLabel) = "en") }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}
    GROUP BY ?film ?filmLabel ?tmdb ?imdb
    """
    for attempt in range(4):
        r = http.get(WIKIDATA_SPARQL, params={"query": query},
                      headers={"User-Agent": WIKIDATA_UA, "Accept": "application/sparql-results+json"},
                      timeout=180)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(15 * (attempt + 1))
            continue
        r.raise_for_status()
        break
    else:
        raise RuntimeError(f"Wikidata query for {qid} kept failing")

    val = lambda b, k: b.get(k, {}).get("value", "")
    rows = []
    for b in r.json()["results"]["bindings"]:
        title = val(b, "filmLabel")
        if re.fullmatch(r"Q\d+", title):   # no English label
            continue
        if not val(b, "tmdb") and not val(b, "year"):   # director duos etc., not films
            continue
        rows.append({
            "rank": "",
            "title": title,
            "year": val(b, "year"),
            "director": val(b, "directors"),
            "note": val(b, "conferred"),
            "url": val(b, "film"),
            "tmdb_id": val(b, "tmdb"),
            "imdb_id": val(b, "imdb"),
        })
    rows.sort(key=lambda r: (r["note"] or r["year"] or "9999", r["title"]))
    return rows


LOOKUP_CACHE = OUT / "_tmdb_lookups.json"


def _norm(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"^(the|a|an|l'|le|la|les)\s+", "", t)
    return re.sub(r"[^a-z0-9]", "", t)


def _search_wikidata_film(title: str) -> list[dict]:
    """Wikidata full-text search restricted to items with a TMDB movie id,
    with every English name (label, aliases, original title) for matching."""
    safe = title.replace('"', " ").replace("\\", " ")
    query = f"""
    SELECT ?item ?ord ?tmdb ?imdb (MIN(YEAR(?pub)) AS ?year)
           (GROUP_CONCAT(DISTINCT ?name; separator="\\u001f") AS ?names) WHERE {{
      SERVICE wikibase:mwapi {{
        bd:serviceParam wikibase:endpoint "www.wikidata.org"; wikibase:api "Search";
                        mwapi:srsearch "{safe} haswbstatement:P4947"; mwapi:srlimit "10" .
        ?item wikibase:apiOutputItem mwapi:title .
        ?ord wikibase:apiOrdinal true .
      }}
      ?item wdt:P4947 ?tmdb .
      OPTIONAL {{ ?item wdt:P345 ?imdb }} OPTIONAL {{ ?item wdt:P577 ?pub }}
      OPTIONAL {{ {{ ?item rdfs:label ?name FILTER(LANG(?name) = "en") }}
                 UNION {{ ?item skos:altLabel ?name FILTER(LANG(?name) = "en") }}
                 UNION {{ ?item wdt:P1476 ?name }} }}
    }} GROUP BY ?item ?ord ?tmdb ?imdb
    """
    for attempt in range(4):
        try:
            r = http.get(WIKIDATA_SPARQL, params={"query": query},
                         headers={"User-Agent": WIKIDATA_UA, "Accept": "application/sparql-results+json"},
                         timeout=30)
        except Exception:
            time.sleep(5)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        val = lambda b, k: b.get(k, {}).get("value", "")
        return [{"names": [n for n in val(b, "names").split("\u001f") if n],
                 "ord": int(val(b, "ord") or 99), "year": val(b, "year"),
                 "tmdb_id": val(b, "tmdb"), "imdb_id": val(b, "imdb")}
                for b in r.json()["results"]["bindings"]]
    return []


def _pick_candidate(title: str, year: int | None, cands: list[dict]) -> dict | None:
    """Best search hit: a release year within ±1 is required whenever the list
    gives one; then title agreement, year closeness, and search rank. A hit
    whose names don't match is accepted only as the top result with the exact year."""
    want = _norm(title)
    best, best_key = None, None
    for c in cands:
        cy = int(c["year"]) if c["year"].isdigit() else None
        if year and (cy is None or abs(cy - year) > 1):
            continue
        names = [_norm(n) for n in c["names"]] or [""]
        score = max((2 if n == want else 1 if n and (want in n or n in want) else 0) for n in names)
        if score == 0 and not (c["ord"] == 0 and year and cy == year):
            continue
        key = (score, -(abs(cy - year) if year and cy else 0), -c["ord"])
        if best_key is None or key > best_key:
            best, best_key = c, key
    return best


def resolve_tmdb_ids(rows: list[dict]) -> None:
    """Fill tmdb_id/imdb_id for rows lacking them via Wikidata search."""
    cache = json.loads(LOOKUP_CACHE.read_text()) if LOOKUP_CACHE.exists() else {}
    unresolved = []
    for row in rows:
        if row.get("tmdb_id"):
            continue
        hint = row.get("year") or row.get("note") or ""   # award lists: the award year
        key = f"{row['title']}|{hint}"
        if key not in cache:
            year = int(hint) if hint.isdigit() else None
            best = _pick_candidate(row["title"], year, _search_wikidata_film(row["title"]))
            cache[key] = ({"tmdb_id": best["tmdb_id"], "imdb_id": best["imdb_id"],
                           "label": (best["names"] or [""])[0], "year": best["year"]} if best else {})
            LOOKUP_CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False, sort_keys=True))
            print(f"  {len(cache)} looked up: {row['title']} -> {cache[key].get('label', '—')}", flush=True)
            time.sleep(0.5)
        hit = cache[key]
        if hit:
            row["tmdb_id"], row["imdb_id"] = hit["tmdb_id"], hit["imdb_id"]
            if not row.get("year") and hit.get("year"):
                row["year"] = hit["year"]
        else:
            unresolved.append(key)
    if unresolved:
        print(f"  no TMDB id for {len(unresolved)}: {unresolved}")


# ── Lists transcribed from Wikipedia tables (scripts/transcribed/*.csv) ───────
# Wikipedia blocks bulk reads from this builder's network, so these were
# transcribed from the pages below; TMDB resolution doubles as a sanity check.

TRANSCRIBED = Path(__file__).resolve().parent / "transcribed"


def _transcribed_source(name: str, url: str):
    def build():
        rows = []
        with (TRANSCRIBED / f"{name}.csv").open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append({
                    "rank": r.get("rank", ""),
                    "title": r["title"],
                    "year": r.get("year", ""),
                    "director": r.get("director", ""),
                    "note": r.get("award_year", ""),
                    "url": url,
                })
        resolve_tmdb_ids(rows)
        write(name, rows)
    return build


def _wikidata_source(name: str, qid: str, prop: str = "P166"):
    def build():
        write(name, _wikidata_films(qid, prop))
    return build


def tspdt_1000():
    """They Shoot Pictures, Don't They? 1,000 Greatest Films (optional source:
    the app ignores this CSV until "tspdt_1000" is added to reference.SOURCES)."""
    soup = BeautifulSoup(fetch("https://theyshootpictures.com/gf1000_all1000films_table.php"), "html.parser")
    rows = []
    for tr in soup.select("table tr")[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(cells) < 6 or not cells[0].isdigit():
            continue
        title = re.sub(r"\s*\[\d{4}\]$", "", cells[2])
        title = re.sub(r"^(.*), (The|A|An|L'|Le|La|Les)$", r"\2 \1", title).replace("L' ", "L'")
        director = " ".join(reversed(cells[3].split(", ", 1))) if ", " in cells[3] and "&" not in cells[3] else cells[3]
        rows.append({"rank": cells[0], "title": title, "year": cells[4], "director": director,
                     "country": cells[5], "url": "https://theyshootpictures.com/gf1000.htm"})
    resolve_tmdb_ids(rows)
    write("tspdt_1000", rows)


SOURCES = {
    "tspdt_1000": tspdt_1000,
    "sight_and_sound_critics_2022": sight_and_sound_critics_2022,
    "sight_and_sound_directors_2022": sight_and_sound_directors_2022,
    "national_film_registry": _wikidata_source("national_film_registry", "Q823422", "P361"),
    "palme_dor": _wikidata_source("palme_dor", "Q179808"),
    "golden_lion": _wikidata_source("golden_lion", "Q209459"),
    "golden_bear": _wikidata_source("golden_bear", "Q154590"),
    "bfi_100": _transcribed_source("bfi_100", "https://en.wikipedia.org/wiki/BFI_Top_100_British_films"),
    "nyfcc_best_film": _transcribed_source(
        "nyfcc_best_film", "https://en.wikipedia.org/wiki/New_York_Film_Critics_Circle_Award_for_Best_Film"),
    "nsfc_best_film": _transcribed_source(
        "nsfc_best_film", "https://en.wikipedia.org/wiki/National_Society_of_Film_Critics_Award_for_Best_Picture"),
    "lafca_best_film": _transcribed_source(
        "lafca_best_film", "https://en.wikipedia.org/wiki/Los_Angeles_Film_Critics_Association_Award_for_Best_Film"),
}


if __name__ == "__main__":
    names = sys.argv[1:] or [n for n in SOURCES if n != "tspdt_1000"]
    for n in names:
        SOURCES[n]()
        time.sleep(2)
