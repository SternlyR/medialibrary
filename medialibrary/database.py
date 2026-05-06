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
        ]:
            try:
                await conn.execute(text(col_sql))
            except Exception:
                pass  # Column already exists
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
