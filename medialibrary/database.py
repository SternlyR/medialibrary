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

    # Ownership
    owned = Column(String(10), default="yes")  # yes, wishlist, sold
    condition = Column(String(50))           # new, like new, good, etc.
    notes = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    movie = relationship("Movie", back_populates="releases")


async def get_engine():
    return create_async_engine(settings.database_url, echo=False)


async def init_db():
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


async def get_session(engine=None) -> AsyncSession:
    if engine is None:
        engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory()
