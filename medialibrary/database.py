"""Database models and session management."""

from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Text, ForeignKey, create_engine
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from medialibrary.config import settings


class Base(DeclarativeBase):
    pass


class Movie(Base):
    """Core movie/film record."""
    __tablename__ = "movies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    year = Column(Integer)
    director = Column(String(500))
    runtime_minutes = Column(Integer)
    mpaa_rating = Column(String(20))   # G, PG, PG-13, R, NC-17, NR
    tmdb_id = Column(Integer, unique=True, index=True)
    imdb_id = Column(String(20), index=True)
    overview = Column(Text)
    genres = Column(String(500))       # comma-separated
    letterboxd_rating = Column(Float)  # 0.5 – 5.0, null if not yet fetched or unrated
    letterboxd_synced_at = Column(DateTime)
    letterboxd_watched = Column(Integer, default=0)  # 0 no, 1 from Letterboxd, 2 set by hand
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    releases = relationship("PhysicalRelease", back_populates="movie")


class PhysicalRelease(Base):
    """A specific physical release (disc) of a movie."""
    __tablename__ = "physical_releases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    movie_id = Column(Integer, ForeignKey("movies.id"), nullable=False)

    # Identification
    upc = Column(String(50), unique=True, index=True)
    bluray_com_id = Column(Integer, index=True)

    # Release details
    format = Column(String(50))              # Blu-Ray, UHD, DVD, 4K UHD
    label = Column(String(200))              # Criterion, A24, Arrow, etc.
    region = Column(String(20))              # US, UK, EU, A, B, C, Free
    physical_release_date = Column(String(20))  # YYYY-MM-DD
    edition = Column(String(200))            # Criterion Spine #42, Director's Cut
    set_name = Column(String(200))           # Alien Anthology, etc.
    disc_count = Column(Integer)
    aspect_ratio = Column(String(20))        # 2.39:1, 1.85:1, etc.

    # Cover art
    cover_url = Column(String(1000))         # Bluray.com front cover URL
    cover_url_back = Column(String(1000))
    cover_url_slip = Column(String(1000))    # slip case front
    cover_url_slipback = Column(String(1000))  # slip case back
    library_cover_url = Column(String(1000)) # overrides cover_url for library card display

    # Ownership
    owned = Column(String(10), default="yes")  # yes, wishlist, sold
    condition = Column(String(50))           # new, like new, good, etc.
    notes = Column(Text)
    films_included = Column(Text)            # JSON array of film titles for box sets

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    movie = relationship("Movie", back_populates="releases")


class WatchlistItem(Base):
    """A disc in the user's ordered watchlist."""
    __tablename__ = "watchlist_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    release_id = Column(Integer, ForeignKey("physical_releases.id"), nullable=False, unique=True)
    position = Column(Integer, nullable=False, default=0)
    kind = Column(String(20), nullable=False, default="unseen")  # unseen | revisit
    # Kept as a tombstone so auto-populate doesn't re-add a disc the user removed.
    removed = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class RefFilm(Base):
    """A film named by at least one reference source (polls, awards, registries)."""
    __tablename__ = "ref_films"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(300), unique=True, index=True)   # tmdb:<id> or t:<title>|<year>
    title = Column(String(500), nullable=False)
    year = Column(Integer)
    director = Column(String(500))
    country = Column(String(200))
    tmdb_id = Column(Integer, index=True)
    imdb_id = Column(String(20))

    entries = relationship("RefEntry", back_populates="film", cascade="all, delete-orphan")


class RefEntry(Base):
    """One appearance of a film in one source: a poll rank, an award, an inclusion."""
    __tablename__ = "ref_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    film_id = Column(Integer, ForeignKey("ref_films.id"), nullable=False, index=True)
    source = Column(String(100), nullable=False, index=True)
    rank = Column(Integer)
    note = Column(String(200))   # award / induction year
    url = Column(String(1000))

    film = relationship("RefFilm", back_populates="entries")


class TmdbPoster(Base):
    """Poster paths cached from TMDB so Discover cards don't re-fetch."""
    __tablename__ = "tmdb_posters"

    tmdb_id = Column(Integer, primary_key=True)
    poster_path = Column(String(300))


class LetterboxdFilm(Base):
    """Every film on the user's Letterboxd watched list (owned or not)."""
    __tablename__ = "letterboxd_films"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    year = Column(Integer)
    slug = Column(String(300))
    rating = Column(Float)


class DiscoverHidden(Base):
    """Recommendations the user dismissed, by RefFilm.key."""
    __tablename__ = "discover_hidden"

    key = Column(String(300), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ChatConversation(Base):
    """An "Ask" conversation: the API message history and what the UI shows."""
    __tablename__ = "chat_conversations"

    id = Column(String(40), primary_key=True)
    title = Column(String(200))
    messages = Column(Text, nullable=False, default="[]")     # Claude API messages, unedited
    transcript = Column(Text, nullable=False, default="[]")   # rendered turns: text, cards, cost
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class StreamingTitle(Base):
    """A film currently on a subscription streaming service (US), per TMDB/JustWatch."""
    __tablename__ = "streaming_titles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    service = Column(String(100), nullable=False, index=True)   # e.g. "Criterion Channel"
    tmdb_id = Column(Integer, nullable=False, index=True)
    title = Column(String(500))
    year = Column(Integer)


class AppMeta(Base):
    __tablename__ = "app_meta"

    key = Column(String(100), primary_key=True)
    value = Column(Text)


class AcquisitionItem(Base):
    """A disc the user wants to buy."""
    __tablename__ = "acquisition_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    year = Column(Integer)
    director = Column(String(500))
    purchase_link = Column(Text)
    format = Column(String(50))     # UHD, Blu-Ray, DVD
    label = Column(String(200))
    tmdb_id = Column(Integer)
    cover_url = Column(String(1000))
    bluray_com_id = Column(Integer)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


async def get_engine():
    return create_async_engine(settings.database_url, echo=False)


async def init_db():
    from sqlalchemy import text
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrations: add new columns to existing databases
        for col_sql in [
            "ALTER TABLE physical_releases ADD COLUMN films_included TEXT",
            "ALTER TABLE physical_releases ADD COLUMN owned TEXT DEFAULT 'yes'",
            "ALTER TABLE physical_releases ADD COLUMN condition TEXT",
            "ALTER TABLE movies ADD COLUMN letterboxd_synced_at DATETIME",
            # Convert empty-string UPCs to NULL so the unique constraint allows
            # multiple no-UPC releases (SQLite permits multiple NULLs in a unique column).
            "UPDATE physical_releases SET upc = NULL WHERE upc = ''",
            "ALTER TABLE physical_releases ADD COLUMN cover_url_slip TEXT",
            "ALTER TABLE physical_releases ADD COLUMN cover_url_slipback TEXT",
            "ALTER TABLE physical_releases ADD COLUMN library_cover_url TEXT",
            "ALTER TABLE movies ADD COLUMN letterboxd_watched INTEGER DEFAULT 0",
            "ALTER TABLE watchlist_items ADD COLUMN kind TEXT NOT NULL DEFAULT 'unseen'",
            "ALTER TABLE acquisition_items ADD COLUMN cover_url TEXT",
            "ALTER TABLE acquisition_items ADD COLUMN bluray_com_id INTEGER",
        ]:
            try:
                await conn.execute(text(col_sql))
            except Exception:
                pass  # Column already exists

        # One-time data fixes, tracked with SQLite's user_version.
        version = (await conn.execute(text("PRAGMA user_version"))).scalar() or 0
        if version < 1:
            # A scraper bug read Letterboxd's hidden ½ glyph as the user's rating
            # for watched-but-unrated films. Those films were watched, so keep that;
            # real ½ ratings come back on the next full sync or export import.
            await conn.execute(text(
                "UPDATE movies SET letterboxd_watched = 1, letterboxd_rating = NULL "
                "WHERE letterboxd_rating = 0.5"
            ))
            import json
            rows = (await conn.execute(text(
                "SELECT id, films_included FROM physical_releases WHERE films_included LIKE '%0.5%'"
            ))).all()
            for rid, raw in rows:
                try:
                    films = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                changed = False
                for f in films if isinstance(films, list) else []:
                    if isinstance(f, dict) and f.get("letterboxd_rating") == 0.5:
                        f["letterboxd_rating"] = None
                        changed = True
                if changed:
                    await conn.execute(
                        text("UPDATE physical_releases SET films_included = :f WHERE id = :i"),
                        {"f": json.dumps(films), "i": rid},
                    )
            await conn.execute(text("PRAGMA user_version = 1"))
    # Seed acquisition_items if empty
    async with engine.begin() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM acquisition_items"))).scalar()
        if count == 0:
            seed = [
                ("2001: A Space Odyssey", 1968, "Stanley Kubrick",  "https://www.ebay.com", "UHD", ""),
                ("Being There",           1979, "Hal Ashby",         "https://www.criterion.com/films/29009-being-there", "Blu-Ray", "Criterion"),
                ("Bonnie & Clyde",        1967, "Arthur Penn",       "https://gruv.com/products/bonnie-and-clyde-blu-ray-_1000122558", "Blu-Ray", ""),
                ("Johnny Guitar",         1954, "Nicholas Ray",      "https://eurekavideo.co.uk/movie/johnny-guitar-standard-edition/", "Blu-Ray", "Eureka"),
                ("Stalker",               1979, "Andrei Tarkovsky",  "https://www.criterion.com/films/28150-stalker", "Blu-Ray", "Criterion"),
                ("The Devils",            1971, "Ken Russell",       "https://www.orbitdvd.com/products/thedevilsoriginalukxversionregionbdvd", "DVD", ""),
                ("There Will Be Blood",   2007, "Paul Thomas Anderson", "https://www.amazon.com/dp/B072ZLL4M2/", "Blu-Ray", ""),
                ("Toy Story",             1995, "John Lasseter",     "https://www.amazon.com/dp/B07PRW64DZ/", "UHD", ""),
                ("Sinners",               2025, "",                  "", "UHD", ""),
            ]
            for title, year, director, link, fmt, label in seed:
                await conn.execute(text(
                    "INSERT INTO acquisition_items (title, year, director, purchase_link, format, label) "
                    "VALUES (:t, :y, :d, :l, :f, :lb)"
                ), {"t": title, "y": year, "d": director, "l": link, "f": fmt, "lb": label})

    return engine


async def get_session(engine=None) -> AsyncSession:
    if engine is None:
        engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory()
