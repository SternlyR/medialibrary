"""Ask the Library: a chat grounded only in the local reference library.

Claude gets read-only tools over the user's discs, Letterboxd watch history
and the reference library (critics' polls, juries, critics' awards, the Film
Registry, Peary, TSPDT). It has no web access, so it cannot pull in review
aggregators or blogs. Films it recommends are shown through `show_films`,
whose cards and citations are built here from stored entries - the model
never writes a citation itself.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

import anthropic
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from medialibrary.config import settings
from medialibrary.database import (
    AcquisitionItem, ChatConversation, LetterboxdFilm, PhysicalRelease, WatchlistItem,
)
from medialibrary.streaming import STREAMING_SERVICES
from medialibrary.reference import (
    SOURCES, TitleYearIndex, build_picks, ensure_reference_loaded, norm_person,
    serialize, split_directors,
)

MODEL = "claude-opus-5-5"
MAX_TOOL_ROUNDS = 10

# Opus 5.5 list prices per token (USD), used only for the per-reply estimate.
PRICE_IN, PRICE_OUT = 4.0 / 1e6, 20.0 / 1e6
PRICE_CACHE_WRITE, PRICE_CACHE_READ = 5.0 / 1e6, 0.20 / 1e6


def _source_lines() -> str:
    return "\n".join(f"- {s.short}: {s.name}" for s in SOURCES.values())


SYSTEM_PROMPT = f"""You are the film adviser inside a private physical-media library app. The owner collects Blu-rays and 4K discs and watches films seriously; talk to them as a well-read film critic and archivist would.

Your only sources of fact about films' critical standing are the tools, which read three things:
1. The reference library: published critics' polls, festival juries, critics' awards, the Library of Congress National Film Registry, and Danny Peary's Cult Movies books. Its sources:
{_source_lines()}
2. The owner's disc library (what they own, by format and label, including films inside box sets).
3. The owner's Letterboxd watch history - used only to know what they have seen and how they rated it. Letterboxd is not a critical source; never present its community ratings or anyone else's opinion from it.
4. Streaming availability in the US for: {", ".join(STREAMING_SERVICES)} (from TMDB / JustWatch, refreshed daily). It says only where a film can be watched today - never treat it as a sign of quality, and say "currently streaming" since catalogs change.

Rules:
- Every claim about a film's critical standing, ranking, award or canonical status must come from a reference entry returned by a tool in this conversation. Name the source when you make the claim (for example "Sight and Sound critics' poll 2022, #41").
- Never cite or invent review-aggregator scores (Rotten Tomatoes, Metacritic, IMDb), blogs, or user-rating sites, and never quote critics or attribute opinions to a named critic unless a reference entry supports it. The reference library holds rankings and awards, not review text, so do not quote reviews.
- You may describe a film's plot, people, era or style from general knowledge, but keep that clearly separate from claims about its reputation, and keep it brief.
- If the reference library has nothing on a film or question, say so plainly rather than filling the gap.
- Whenever you recommend or discuss specific films, call show_films with them (at most 8, best first), giving each a one-line reason grounded in the tool results and the owner's tastes. The app renders those as cards with the stored citations, so do not repeat every citation in your text.
- "Blind buy" means a film the owner has not seen and does not own; "add to shelf" means seen but not owned. Check ownership and watch history with the tools before recommending, and never recommend something they already own unless asked.
- You cannot change the library. If the owner wants to buy or track a film, point them to the card's "+ Acquire list" button.
- They read on a phone: lead with the answer, keep prose short (a few sentences or a tight list), no headings.
"""

STATUS_ENUM = ["any", "unseen", "seen_not_owned", "not_owned", "owned", "seen"]

TOOLS = [
    {
        "name": "search_reference",
        "description": (
            "Search the reference library of canonical films (polls, juries, critics' awards, Film Registry, "
            "Peary's Cult Movies). Each result has its citations plus the owner's status: owned, watched, their "
            "Letterboxd rating, whether it is on their Acquire list, and where it is streaming now. Results are sorted by reference strength "
            "(how many sources back a film and how highly) blended with the owner's affinity for its director."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to match in the title or director. Optional."},
                "director": {"type": "string", "description": "Director name (or part of it). Optional."},
                "source": {"type": "string", "enum": list(SOURCES), "description": "Only films cited by this source."},
                "year_from": {"type": "integer"},
                "year_to": {"type": "integer"},
                "country": {"type": "string", "description": "Production country (only polls record it; matches partially)."},
                "status": {"type": "string", "enum": STATUS_ENUM,
                           "description": "unseen = not watched and not owned; seen_not_owned = watched but not on the shelf."},
                "streaming_on": {"type": "string", "enum": STREAMING_SERVICES,
                                 "description": "Only films currently streaming on this service (US)."},
                "limit": {"type": "integer", "description": "Max results, default 15, at most 40."},
            },
            "required": [],
        },
    },
    {
        "name": "get_film",
        "description": (
            "Everything the app knows about one film: its reference citations, whether the owner owns it (which "
            "disc, format, label), whether and how they rated it on Letterboxd, whether it is on their "
            "Watchlist or Acquire list, and where it is streaming now."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "year": {"type": "integer", "description": "Release year, if known - helps tell remakes apart."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "search_library",
        "description": "Search the owner's disc library. Includes films inside box sets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to match in the title or director."},
                "director": {"type": "string"},
                "label": {"type": "string", "description": "Disc label, e.g. Criterion, Arrow."},
                "format": {"type": "string", "enum": ["UHD", "Blu-Ray", "DVD"]},
                "watched": {"type": "boolean", "description": "true = only watched, false = only unwatched."},
                "limit": {"type": "integer", "description": "Max results, default 25, at most 60."},
            },
            "required": [],
        },
    },
    {
        "name": "search_watch_history",
        "description": (
            "Search the owner's Letterboxd watch history (every film they have logged as watched, owned or not) "
            "with their own star rating, if any. Use it to learn their taste or check whether they have seen something."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to match in the title."},
                "min_rating": {"type": "number", "description": "Only films they rated at least this (0.5-5)."},
                "max_rating": {"type": "number"},
                "unrated_only": {"type": "boolean"},
                "year_from": {"type": "integer"},
                "year_to": {"type": "integer"},
                "limit": {"type": "integer", "description": "Max results, default 30, at most 100."},
            },
            "required": [],
        },
    },
    {
        "name": "taste_profile",
        "description": (
            "A summary of the owner's taste: library size, most-collected directors and labels, the directors they "
            "rate highest, and how much of each reference source they have seen and own."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "show_films",
        "description": (
            "Display films to the owner as cards built from the reference library, with stored citations and "
            "buttons to add them to the Acquire list. Call this for every set of films you recommend or discuss, "
            "best first, at most 8."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "films": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "year": {"type": "integer"},
                            "reason": {"type": "string", "description": "One line on why, for this owner."},
                        },
                        "required": ["title"],
                    },
                },
            },
            "required": ["films"],
        },
    },
]


def _clip(limit, default: int, maximum: int) -> int:
    try:
        return max(1, min(int(limit or default), maximum))
    except (TypeError, ValueError):
        return default


class TurnContext:
    """Data shared by every tool call within one reply."""

    def __init__(self, session):
        self.session = session
        self.picks = None
        self.coverage = None
        self.cards: list[dict] = []

    async def load_picks(self):
        if self.picks is None:
            await ensure_reference_loaded(self.session)
            self.picks, self.coverage = await build_picks(self.session)
            self.by_tmdb = {p.film.tmdb_id: p for p in self.picks if p.film.tmdb_id}
            self.by_title = TitleYearIndex()
            for p in self.picks:
                self.by_title.add(p.film.title, p.film.year, p)
        return self.picks

    def find_pick(self, title: str, year: Optional[int]):
        hit = self.by_title.get(title, year)
        if hit is None and year:
            hit = self.by_title.get(title, None)
        return hit

    async def releases(self):
        return (await self.session.execute(
            select(PhysicalRelease).options(selectinload(PhysicalRelease.movie))
        )).scalars().all()


def _pick_summary(p) -> dict:
    return {
        "title": p.film.title, "year": p.film.year, "director": p.film.director or None,
        "country": p.film.country or None,
        "citations": [c["source"] + (f" — {c['label']}" if c["label"] else "") for c in p.citations],
        "owned": p.owned, "watched": p.watched, "owner_rating": p.rating,
        "on_acquire_list": p.on_acquire, "hidden_by_owner": p.hidden,
        "streaming_now": p.streaming or None,
        "reference_score": p.score,
    }


def _status_ok(p, status: str) -> bool:
    return {
        "any": True,
        "unseen": not p.watched and not p.owned,
        "seen_not_owned": p.watched and not p.owned,
        "not_owned": not p.owned,
        "owned": p.owned,
        "seen": p.watched,
    }.get(status or "any", True)


async def tool_search_reference(ctx: TurnContext, a: dict) -> dict:
    picks = await ctx.load_picks()
    q = (a.get("query") or "").lower()
    director = norm_person(a.get("director") or "")
    country = (a.get("country") or "").lower()
    out = []
    for p in picks:
        f = p.film
        if a.get("source") and not any(e.source == a["source"] for e in f.entries):
            continue
        if q and q not in (f.title or "").lower() and q not in (f.director or "").lower():
            continue
        if director and not any(director in d for d in split_directors(f.director)):
            continue
        if a.get("year_from") and (not f.year or f.year < a["year_from"]):
            continue
        if a.get("year_to") and (not f.year or f.year > a["year_to"]):
            continue
        if country and country not in (f.country or "").lower():
            continue
        if not _status_ok(p, a.get("status")):
            continue
        if a.get("streaming_on") and a["streaming_on"] not in p.streaming:
            continue
        out.append(p)
    out.sort(key=lambda p: -p.score)
    limit = _clip(a.get("limit"), 15, 40)
    return {"total_matches": len(out), "films": [_pick_summary(p) for p in out[:limit]]}


async def tool_get_film(ctx: TurnContext, a: dict) -> dict:
    await ctx.load_picks()
    title, year = a["title"], a.get("year")
    pick = ctx.find_pick(title, year)
    result: dict = {"reference": _pick_summary(pick) if pick else None}
    if not pick:
        result["note"] = "Not in the reference library - no poll, award or registry entry is stored for it."

    discs = []
    lib = TitleYearIndex()
    for r in await ctx.releases():
        m = r.movie
        if not m:
            continue
        lib.add(m.title, m.year, (r, None))
        try:
            for inc in json.loads(r.films_included or "[]"):
                if isinstance(inc, dict) and inc.get("title"):
                    lib.add(inc["title"], inc.get("year"), (r, inc))
        except ValueError:
            pass
    found = lib.get(title, year) or (lib.get(title, None) if year else None)
    hit = found[0] if found else None
    if found:
        r, inc = found
        disc = {"release_title": r.movie.title, "format": r.format, "label": r.label, "edition": r.edition}
        if inc is None:   # a single-film disc: its own Letterboxd status
            disc["owner_rating"] = r.movie.letterboxd_rating
            disc["owner_watched"] = bool(r.movie.letterboxd_watched or r.movie.letterboxd_rating)
        else:             # a film inside a box set
            disc["in_box_set"] = True
            disc["owner_rating"] = inc.get("letterboxd_rating")
        discs.append(disc)
    result["owned_discs"] = discs

    hist = TitleYearIndex()
    for lf in (await ctx.session.execute(select(LetterboxdFilm))).scalars().all():
        hist.add(lf.title, lf.year, lf)
    seen = hist.get(title, year)
    result["watch_history"] = ({"watched": True, "owner_rating": seen.rating} if seen
                               else {"watched": False})

    acq = TitleYearIndex()
    for item in (await ctx.session.execute(select(AcquisitionItem))).scalars().all():
        acq.add(item.title, item.year, item)
    a_hit = acq.get(title, year)
    result["acquire_list"] = ({"format": a_hit.format, "label": a_hit.label} if a_hit else None)
    if hit:
        on_wl = (await ctx.session.execute(
            select(WatchlistItem).where(WatchlistItem.release_id == hit.id, WatchlistItem.removed == 0)
        )).scalar_one_or_none()
        result["on_watchlist"] = bool(on_wl)
    return result


async def tool_search_library(ctx: TurnContext, a: dict) -> dict:
    q = (a.get("query") or "").lower()
    director = norm_person(a.get("director") or "")
    label = (a.get("label") or "").lower()
    rows = []
    for r in await ctx.releases():
        m = r.movie
        if not m:
            continue
        films = []
        try:
            films = [f for f in json.loads(r.films_included or "[]") if isinstance(f, dict)]
        except ValueError:
            pass
        text = " ".join([m.title or "", m.director or ""] + [f.get("title", "") for f in films]).lower()
        if q and q not in text:
            continue
        if director and not any(director in d for d in split_directors(
                " / ".join([m.director or ""] + [f.get("director") or "" for f in films]))):
            continue
        if label and label not in (r.label or "").lower():
            continue
        if a.get("format") and r.format != a["format"]:
            continue
        watched = bool(m.letterboxd_watched or m.letterboxd_rating)
        if a.get("watched") is not None and watched != a["watched"]:
            continue
        rows.append({
            "title": m.title, "year": m.year, "director": m.director, "format": r.format,
            "label": r.label, "edition": r.edition, "watched": watched, "owner_rating": m.letterboxd_rating,
            "box_set_films": [f"{f.get('title')} ({f.get('year')})" for f in films] or None,
        })
    rows.sort(key=lambda x: (x["title"] or "").lower())
    limit = _clip(a.get("limit"), 25, 60)
    return {"total_matches": len(rows), "discs": rows[:limit]}


async def tool_search_watch_history(ctx: TurnContext, a: dict) -> dict:
    q = (a.get("query") or "").lower()
    rows = []
    for lf in (await ctx.session.execute(select(LetterboxdFilm))).scalars().all():
        if q and q not in (lf.title or "").lower():
            continue
        if a.get("unrated_only") and lf.rating is not None:
            continue
        if a.get("min_rating") is not None and (lf.rating is None or lf.rating < a["min_rating"]):
            continue
        if a.get("max_rating") is not None and (lf.rating is None or lf.rating > a["max_rating"]):
            continue
        if a.get("year_from") and (not lf.year or lf.year < a["year_from"]):
            continue
        if a.get("year_to") and (not lf.year or lf.year > a["year_to"]):
            continue
        rows.append({"title": lf.title, "year": lf.year, "owner_rating": lf.rating})
    rows.sort(key=lambda x: (-(x["owner_rating"] or 0), x["title"]))
    limit = _clip(a.get("limit"), 30, 100)
    total = len((await ctx.session.execute(select(LetterboxdFilm.id))).all())
    return {"history_size": total, "total_matches": len(rows), "films": rows[:limit]}


async def tool_taste_profile(ctx: TurnContext, a: dict) -> dict:
    picks = await ctx.load_picks()
    releases = await ctx.releases()
    owned_dirs: dict[str, int] = {}
    labels: dict[str, int] = {}
    for r in releases:
        if r.label:
            labels[r.label] = labels.get(r.label, 0) + 1
        try:
            inside = [f for f in json.loads(r.films_included or "[]") if isinstance(f, dict)]
        except ValueError:
            inside = []
        # A box set's own director field just repeats its films' directors.
        names = [f.get("director") for f in inside] if inside else ([r.movie.director] if r.movie else [])
        for n in names:
            for d in (n or "").replace(" / ", ",").split(","):
                d = d.strip()
                if d:
                    owned_dirs[d] = owned_dirs.get(d, 0) + 1
    rated: dict[str, list[float]] = {}
    for p in picks:
        if p.rating and p.film.director:
            rated.setdefault(p.film.director.split(",")[0].strip(), []).append(p.rating)
    top_rated = sorted(((d, sum(v) / len(v), len(v)) for d, v in rated.items() if len(v) >= 2),
                       key=lambda x: (-x[1], -x[2]))[:15]
    history = (await ctx.session.execute(select(LetterboxdFilm))).scalars().all()
    return {
        "discs_owned": len(releases),
        "films_logged_on_letterboxd": len(history),
        "films_rated": sum(1 for h in history if h.rating is not None),
        "most_collected_directors": sorted(owned_dirs.items(), key=lambda x: -x[1])[:15],
        "most_collected_labels": sorted(labels.items(), key=lambda x: -x[1])[:10],
        "highest_rated_directors_among_reference_films": [
            {"director": d, "avg_rating": round(avg, 2), "films_rated": n} for d, avg, n in top_rated],
        "reference_coverage": {
            SOURCES[k].short: v for k, v in (ctx.coverage or {}).items() if v.get("total")},
    }


async def tool_show_films(ctx: TurnContext, a: dict) -> dict:
    from medialibrary.server import _poster_paths   # avoid a circular import at module load
    await ctx.load_picks()
    shown, missing, picks = [], [], []
    for f in (a.get("films") or [])[:8]:
        p = ctx.find_pick(f.get("title", ""), f.get("year"))
        if p:
            picks.append((p, f.get("reason") or ""))
        else:
            missing.append(f.get("title"))
    posters = await _poster_paths(ctx.session, [p.film.tmdb_id for p, _ in picks])
    for p, reason in picks:
        card = serialize(p, settings.tmdb_image_base, posters)
        card["note"] = reason
        ctx.cards.append(card)
        shown.append(p.film.title)
    result = {"shown": shown}
    if missing:
        result["not_in_reference_library"] = missing
        result["note"] = ("These have no reference-library entry, so no card was shown; mention them only "
                          "without claiming any critical standing.")
    return result


TOOL_FUNCS = {
    "search_reference": tool_search_reference,
    "get_film": tool_get_film,
    "search_library": tool_search_library,
    "search_watch_history": tool_search_watch_history,
    "taste_profile": tool_taste_profile,
    "show_films": tool_show_films,
}

TOOL_LABELS = {
    "search_reference": "reference library",
    "get_film": "film details",
    "search_library": "your discs",
    "search_watch_history": "your watch history",
    "taste_profile": "your taste profile",
    "show_films": "film cards",
}


def is_configured() -> bool:
    return bool(settings.anthropic_api_key)


def _cost(usage) -> float:
    return (
        (usage.input_tokens or 0) * PRICE_IN
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * PRICE_CACHE_WRITE
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * PRICE_CACHE_READ
        + (usage.output_tokens or 0) * PRICE_OUT
    )


async def ask(session, conversation_id: Optional[str], text: str) -> dict:
    """Run one user turn: the model may call tools several times before answering."""
    convo = await session.get(ChatConversation, conversation_id) if conversation_id else None
    if convo is None:
        convo = ChatConversation(id=uuid.uuid4().hex, title=text[:80], messages="[]", transcript="[]")
        session.add(convo)
    messages = json.loads(convo.messages)
    transcript = json.loads(convo.transcript)
    messages.append({"role": "user", "content": text})

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    ctx = TurnContext(session)
    tools_used: list[str] = []
    cost = 0.0
    reply = ""
    notice = None

    for _ in range(MAX_TOOL_ROUNDS):
        response = await client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": "medium"},
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        cost += _cost(response.usage)
        # Append unchanged: thinking blocks must be passed back exactly as returned.
        messages.append({"role": "assistant", "content": [b.to_dict() for b in response.content]})

        if response.stop_reason == "refusal":
            notice = "Claude declined to answer this one. Try rephrasing the question."
            break
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            reply = "\n\n".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "max_tokens":
                notice = "The answer was cut off - ask for a shorter answer."
            break

        results = []
        for tu in tool_uses:
            func = TOOL_FUNCS.get(tu.name)
            if tu.name not in tools_used:
                tools_used.append(tu.name)
            try:
                if func is None:
                    raise ValueError(f"Unknown tool {tu.name}")
                out = await func(ctx, dict(tu.input or {}))
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": json.dumps(out, ensure_ascii=False, default=str)})
            except Exception as e:  # report to the model instead of failing the turn
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": f"Error: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})
    else:
        notice = "Stopped after too many lookups - try a narrower question."

    transcript.append({"role": "user", "text": text})
    transcript.append({
        "role": "assistant", "text": reply, "cards": ctx.cards, "notice": notice,
        "sources_used": [TOOL_LABELS[t] for t in tools_used if t in TOOL_LABELS and t != "show_films"],
        "cost": round(cost, 4),
    })
    convo.messages = json.dumps(messages, ensure_ascii=False)
    convo.transcript = json.dumps(transcript, ensure_ascii=False)
    convo.updated_at = datetime.utcnow()
    await session.commit()
    return {"conversation_id": convo.id, "turn": transcript[-1]}
