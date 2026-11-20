"""Reference library and the Discover recommendation engine.

The reference library is a closed set of published, citable sources (critics'
polls, festival prizes, critics' awards, the National Film Registry), stored as
CSVs in data/reference/ by scripts/build_reference.py and loaded into the
ref_films / ref_entries tables. Recommendations are computed only from those
entries plus the user's own library and Letterboxd watch history - no review
aggregators, no user-rating sites, no model-generated opinions.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from medialibrary.database import (
    AcquisitionItem, AppMeta, DiscoverHidden, LetterboxdFilm, Movie, PhysicalRelease, RefEntry, RefFilm,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    short: str
    weight: float
    url: str
    ranked: bool = False


SOURCES: dict[str, Source] = {s.key: s for s in [
    Source("sight_and_sound_critics_2022", "Sight and Sound Greatest Films poll 2022 (critics)",
           "S&S critics 2022", 3.0,
           "https://www.bfi.org.uk/sight-and-sound/greatest-films-all-time", ranked=True),
    Source("sight_and_sound_directors_2022", "Sight and Sound Greatest Films poll 2022 (directors)",
           "S&S directors 2022", 2.5,
           "https://www.bfi.org.uk/sight-and-sound/directors-100-greatest-films-all-time", ranked=True),
    Source("bfi_100", "BFI 100 greatest British films (1999)", "BFI 100", 1.25,
           "https://www.bfi.org.uk/", ranked=True),
    Source("palme_dor", "Palme d'Or, Cannes Film Festival", "Palme d'Or", 1.5,
           "https://www.festival-cannes.com/"),
    Source("golden_lion", "Golden Lion, Venice Film Festival", "Golden Lion", 1.25,
           "https://www.labiennale.org/en/cinema"),
    Source("golden_bear", "Golden Bear, Berlin International Film Festival", "Golden Bear", 1.0,
           "https://www.berlinale.de/"),
    Source("nyfcc_best_film", "New York Film Critics Circle, Best Film", "NYFCC Best Film", 1.0,
           "https://www.nyfcc.com/"),
    Source("nsfc_best_film", "National Society of Film Critics, Best Film", "NSFC Best Film", 1.0,
           "https://www.nationalsocietyoffilmcritics.com/"),
    Source("lafca_best_film", "Los Angeles Film Critics Association, Best Film", "LAFCA Best Film", 0.75,
           "https://www.lafca.net/"),
    Source("peary_cult_movies", "Danny Peary, Cult Movies (vols. 1–3, 1981–88)", "Peary Cult Movies", 0.75,
           "https://en.wikipedia.org/wiki/Cult_Movies_(book)"),
    Source("national_film_registry", "National Film Registry, Library of Congress", "Film Registry", 1.0,
           "https://www.loc.gov/programs/national-film-preservation-board/film-registry/"),
]}


# ── Normalisation and matching ────────────────────────────────────────────────

def norm_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"^(.*), (the|a|an)$", r"\2 \1", t)
    t = re.sub(r"^(the|a|an|l'|le|la|les)\s*", "", t)
    return re.sub(r"[^a-z0-9]", "", t)


def norm_person(name: str) -> str:
    t = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", t).strip()


def split_directors(field_: str) -> list[str]:
    parts = re.split(r",|&|/|\band\b", field_ or "")
    return [p for p in (norm_person(x) for x in parts) if p]


def _clean_title(title: str) -> str:
    """Drop trailing "(1960)" / "[4K]" style suffixes from library titles."""
    t = title or ""
    while True:
        new = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*$", "", t)
        if new == t:
            return t
        t = new


class TitleYearIndex:
    """Lookup by normalised title with a ±1 year tolerance."""

    def __init__(self):
        self._by_title: dict[str, list[tuple[Optional[int], object]]] = defaultdict(list)

    def add(self, title: str, year: Optional[int], value) -> None:
        if title:
            self._by_title[norm_title(title)].append((year, value))

    def get(self, title: str, year: Optional[int]):
        cands = self._by_title.get(norm_title(title), [])
        if not cands:
            return None
        if year:
            close = [v for y, v in cands if y and abs(y - year) <= 1]
            if close:
                return close[0]
            undated = [v for y, v in cands if not y]
            return undated[0] if undated else None
        return cands[0][1]


# ── Import ────────────────────────────────────────────────────────────────────

def _csv_hash() -> str:
    h = hashlib.sha256()
    for path in sorted(DATA_DIR.glob("*.csv")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def _int(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


async def ensure_reference_loaded(session) -> int:
    """(Re)load data/reference/*.csv into ref_films/ref_entries when the files
    changed since the last load. Returns the number of reference films."""
    digest = _csv_hash()
    meta = await session.get(AppMeta, "reference_hash")
    if meta and meta.value == digest:
        return len((await session.execute(select(RefFilm.id))).all())

    films: dict[str, dict] = {}
    by_tmdb: dict[int, str] = {}
    by_title = TitleYearIndex()

    def film_for(row) -> str:
        tmdb = _int(row.get("tmdb_id"))
        year = _int(row.get("year"))
        if tmdb and tmdb in by_tmdb:
            return by_tmdb[tmdb]
        existing = by_title.get(row["title"], year)
        if existing and (not tmdb or not films[existing]["tmdb_id"]):
            if tmdb:
                films[existing]["tmdb_id"] = tmdb
                by_tmdb[tmdb] = existing
            return existing
        key = f"tmdb:{tmdb}" if tmdb else f"t:{norm_title(row['title'])}|{year or ''}"
        films[key] = {
            "key": key, "title": row["title"], "year": year,
            "director": row.get("director") or "", "country": row.get("country") or "",
            "tmdb_id": tmdb, "imdb_id": row.get("imdb_id") or None, "entries": [],
        }
        if tmdb:
            by_tmdb[tmdb] = key
        by_title.add(row["title"], year, key)
        return key

    # Ranked polls first so their (cleaner) titles and directors win.
    order = sorted(DATA_DIR.glob("*.csv"), key=lambda p: (not SOURCES.get(p.stem, Source("", "", "", 0, "")).ranked, p.stem))
    for path in order:
        if path.stem not in SOURCES:
            continue
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = film_for(row)
                film = films[key]
                if not film["director"] and row.get("director"):
                    film["director"] = row["director"]
                film["entries"].append({
                    "source": path.stem, "rank": _int(row.get("rank")),
                    "note": row.get("note") or None, "url": row.get("url") or None,
                })

    await session.execute(delete(RefEntry))
    await session.execute(delete(RefFilm))
    for f in films.values():
        rf = RefFilm(**{k: v for k, v in f.items() if k != "entries"})
        rf.entries = [RefEntry(**e) for e in f["entries"]]
        session.add(rf)
    if meta:
        meta.value = digest
    else:
        session.add(AppMeta(key="reference_hash", value=digest))
    await session.commit()
    return len(films)


async def store_letterboxd_films(session, films) -> None:
    """Replace the stored Letterboxd watched list (WatchedFilm-like objects)."""
    await session.execute(delete(LetterboxdFilm))
    seen = set()
    for f in films:
        k = (norm_title(f.title), f.year)
        if k in seen:
            continue
        seen.add(k)
        session.add(LetterboxdFilm(title=f.title, year=f.year, slug=getattr(f, "slug", "") or "",
                                   rating=f.rating))


# ── Recommendations ───────────────────────────────────────────────────────────

@dataclass
class Pick:
    film: RefFilm
    canon: float
    affinity: float = 1.0
    owned: bool = False
    watched: bool = False
    rating: Optional[float] = None
    on_acquire: bool = False
    hidden: bool = False   # user marked "Not interested"
    reasons: list[str] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)

    @property
    def score(self) -> float:
        bonus = 1.0
        if self.rating:
            bonus = 1 + 0.3 * (self.rating - 3)
        return round(self.canon * self.affinity * bonus, 2)


def _entry_label(src: Source, e: RefEntry) -> str:
    if src.ranked and e.rank:
        return f"{src.short} #{e.rank}"
    if e.note:
        return f"{src.short} {e.note}"
    return src.short


def _entry_weight(src: Source, e: RefEntry, list_size: int) -> float:
    if src.ranked and e.rank and list_size:
        return src.weight * (1 + (1 - (e.rank - 1) / list_size))   # #1 → 2×, last → ~1×
    return src.weight


async def build_picks(session) -> tuple[list[Pick], dict]:
    films = (await session.execute(
        select(RefFilm).options(selectinload(RefFilm.entries))
    )).scalars().all()
    list_sizes = defaultdict(int)
    for f in films:
        for e in f.entries:
            list_sizes[e.source] += 1

    # What the user owns: library movies plus films inside box sets.
    releases = (await session.execute(
        select(PhysicalRelease).options(selectinload(PhysicalRelease.movie))
    )).scalars().all()
    owned_tmdb: set[int] = set()
    owned_titles = TitleYearIndex()
    owned_by_director: dict[str, int] = defaultdict(int)
    ratings_by_director: dict[str, list[float]] = defaultdict(list)
    library_rating: dict[int, float] = {}
    for r in releases:
        m = r.movie
        if not m:
            continue
        try:
            included = json.loads(r.films_included or "[]")
        except ValueError:
            included = []
        included = included if isinstance(included, list) else []

        if m.tmdb_id:
            owned_tmdb.add(m.tmdb_id)
            if m.letterboxd_rating:
                library_rating[m.tmdb_id] = m.letterboxd_rating
        owned_titles.add(_clean_title(m.title), m.year, True)
        # "Police Story / Police Story 2" style sets name their films in the title.
        if " / " in (m.title or ""):
            for part in m.title.split(" / "):
                owned_titles.add(_clean_title(part), None, True)
        if not included:
            for d in split_directors(m.director):
                owned_by_director[d] += 1
                if m.letterboxd_rating:
                    ratings_by_director[d].append(m.letterboxd_rating)

        # Box sets: every film inside counts as owned, and towards its director.
        for inc in included:
            if isinstance(inc, str):
                inc = {"title": inc}
            if not isinstance(inc, dict) or not inc.get("title"):
                continue
            if _int(inc.get("tmdb_id")):
                owned_tmdb.add(_int(inc["tmdb_id"]))
            owned_titles.add(_clean_title(inc["title"]), _int(inc.get("year")), True)
            for d in split_directors(inc.get("director") or ""):
                owned_by_director[d] += 1
                if inc.get("letterboxd_rating"):
                    ratings_by_director[d].append(inc["letterboxd_rating"])

    watched = TitleYearIndex()
    for lf in (await session.execute(select(LetterboxdFilm))).scalars().all():
        watched.add(lf.title, lf.year, lf.rating if lf.rating is not None else 0.0)
    watched_movies = {
        m.tmdb_id: m.letterboxd_rating
        for m in (await session.execute(select(Movie))).scalars().all()
        if m.tmdb_id and (m.letterboxd_watched or m.letterboxd_rating)
    }

    hidden = {h.key for h in (await session.execute(select(DiscoverHidden))).scalars().all()}

    # Films already on the To Acquire list stay listed but are flagged as such.
    acquire_tmdb: set[int] = set()
    acquire_titles = TitleYearIndex()
    for a in (await session.execute(select(AcquisitionItem))).scalars().all():
        if a.tmdb_id:
            acquire_tmdb.add(a.tmdb_id)
        acquire_titles.add(_clean_title(a.title), a.year, True)

    picks: list[Pick] = []
    coverage = {k: {"total": 0, "watched": 0, "owned": 0} for k in SOURCES}
    for f in films:
        entries = [e for e in f.entries if e.source in SOURCES]
        if not entries:
            continue
        owned = bool((f.tmdb_id and f.tmdb_id in owned_tmdb) or owned_titles.get(f.title, f.year))
        w = watched.get(f.title, f.year)
        rating = (w or None) if w is not None else None
        is_watched = w is not None
        if f.tmdb_id and f.tmdb_id in watched_movies:
            is_watched = True
            rating = rating or watched_movies[f.tmdb_id]
        if f.tmdb_id and f.tmdb_id in library_rating:
            rating = rating or library_rating[f.tmdb_id]

        for e in entries:
            cov = coverage[e.source]
            cov["total"] += 1
            cov["watched"] += is_watched
            cov["owned"] += owned

        canon = sum(_entry_weight(SOURCES[e.source], e, list_sizes[e.source]) for e in entries)
        pick = Pick(film=f, canon=canon, owned=owned, watched=is_watched, rating=rating,
                    hidden=f.key in hidden, on_acquire=bool((f.tmdb_id and f.tmdb_id in acquire_tmdb) or acquire_titles.get(f.title, f.year)))
        for e in sorted(entries, key=lambda e: -SOURCES[e.source].weight):
            src = SOURCES[e.source]
            label = _entry_label(src, e)
            pick.reasons.append(label)
            pick.citations.append({"label": label, "source": src.name, "url": e.url or src.url})

        # Affinity: directors the user already collects or rates highly.
        best_note = None
        for d in split_directors(f.director):
            n_owned = owned_by_director.get(d, 0)
            rs = ratings_by_director.get(d, [])
            boost = 0.12 * min(n_owned, 5)
            if len(rs) >= 2:
                boost += 0.15 * (sum(rs) / len(rs) - 3)
            if boost > pick.affinity - 1:
                pick.affinity = max(0.7, min(2.0, 1 + boost))
                who = f.director.split(",")[0].strip()
                if n_owned and rs:
                    best_note = f"You own {n_owned} {who} film{'s' if n_owned != 1 else ''}, rated {sum(rs)/len(rs):.1f}★ on average"
                elif n_owned:
                    best_note = f"You own {n_owned} {who} film{'s' if n_owned != 1 else ''}"
        if best_note:
            pick.reasons.append(best_note)
        picks.append(pick)

    return picks, coverage


def serialize(p: Pick, poster_base: str, posters: dict[int, str]) -> dict:
    f = p.film
    poster = posters.get(f.tmdb_id or 0)
    return {
        "key": f.key, "title": f.title, "year": f.year, "director": f.director,
        "tmdb_id": f.tmdb_id, "imdb_id": f.imdb_id,
        "poster_url": f"{poster_base}{poster}" if poster else "",
        "score": p.score, "owned": p.owned, "watched": p.watched, "rating": p.rating,
        "on_acquire": p.on_acquire, "hidden": p.hidden,
        "reasons": p.reasons, "citations": p.citations,
    }
