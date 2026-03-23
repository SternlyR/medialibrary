"""Command-line interface for the media library metadata tool.

Usage examples:
    # Look up by UPC barcode (scan with phone, type in)
    python -m medialibrary.cli lookup --upc 025192251498

    # Look up by title
    python -m medialibrary.cli lookup --title "Alien" --year 1979

    # Look up by Blu-ray.com ID (from URL: blu-ray.com/movies/details.aspx?id=XXXXX)
    python -m medialibrary.cli lookup --bluray-id 12345

    # Add to library database
    python -m medialibrary.cli add --upc 025192251498

    # List all items in the library
    python -m medialibrary.cli list

    # Export to CSV for Google Sheets import
    python -m medialibrary.cli export --output library.csv
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich import print as rprint

from medialibrary.metadata import enrich, EnrichedRelease

app = typer.Typer(
    name="medialibrary",
    help="Physical media library metadata tool — look up disc metadata by UPC or title.",
    add_completion=False,
)
console = Console()


# ── lookup ────────────────────────────────────────────────────────────────────

@app.command()
def lookup(
    upc: Optional[str] = typer.Option(None, "--upc", "-u", help="UPC barcode from the disc case"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Movie title"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Release year"),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="Label/studio (e.g. Criterion)"),
    bluray_id: Optional[int] = typer.Option(None, "--bluray-id", help="Blu-ray.com movie ID"),
    tmdb_id: Optional[int] = typer.Option(None, "--tmdb-id", help="TMDB movie ID"),
    output_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
    output_sheets: bool = typer.Option(False, "--sheets", help="Output Google Sheets row"),
):
    """Look up metadata for a physical release without saving to the database."""
    if not any([upc, title, bluray_id, tmdb_id]):
        console.print("[red]Error:[/red] Provide at least one of: --upc, --title, --bluray-id, --tmdb-id")
        raise typer.Exit(1)

    result = asyncio.run(enrich(
        upc=upc,
        title=title,
        year=year,
        label=label,
        bluray_com_id=bluray_id,
        tmdb_id=tmdb_id,
    ))

    if output_json:
        print(json.dumps(result.to_dict(), indent=2))
        return

    if output_sheets:
        _print_sheets_row(result)
        return

    _print_result(result)


# ── add ───────────────────────────────────────────────────────────────────────

@app.command()
def add(
    upc: Optional[str] = typer.Option(None, "--upc", "-u"),
    title: Optional[str] = typer.Option(None, "--title", "-t"),
    year: Optional[int] = typer.Option(None, "--year", "-y"),
    label: Optional[str] = typer.Option(None, "--label", "-l"),
    bluray_id: Optional[int] = typer.Option(None, "--bluray-id"),
    tmdb_id: Optional[int] = typer.Option(None, "--tmdb-id"),
    set_name: Optional[str] = typer.Option(None, "--set", help="Box set name (e.g. 'Alien Anthology')"),
):
    """Look up metadata and save the release to the local database."""
    from medialibrary.database import init_db, get_session, Movie, PhysicalRelease
    from sqlalchemy import select

    if not any([upc, title, bluray_id, tmdb_id]):
        console.print("[red]Error:[/red] Provide at least one of: --upc, --title, --bluray-id, --tmdb-id")
        raise typer.Exit(1)

    async def _add():
        result = await enrich(
            upc=upc, title=title, year=year, label=label,
            bluray_com_id=bluray_id, tmdb_id=tmdb_id,
        )
        if set_name:
            result.set_name = set_name

        _print_result(result)

        engine = await init_db()
        async with await get_session(engine) as session:
            # Upsert Movie
            movie = None
            if result.tmdb_id:
                stmt = select(Movie).where(Movie.tmdb_id == result.tmdb_id)
                movie = (await session.execute(stmt)).scalar_one_or_none()

            if not movie:
                movie = Movie(
                    title=result.title,
                    year=result.year,
                    director=result.director,
                    runtime_minutes=result.runtime_minutes,
                    mpaa_rating=result.mpaa_rating,
                    tmdb_id=result.tmdb_id,
                    imdb_id=result.imdb_id,
                    overview=result.overview,
                    genres=result.genres,
                )
                session.add(movie)
                await session.flush()
            else:
                # Update existing
                movie.director = result.director or movie.director
                movie.runtime_minutes = result.runtime_minutes or movie.runtime_minutes
                movie.mpaa_rating = result.mpaa_rating or movie.mpaa_rating
                movie.genres = result.genres or movie.genres

            # Upsert PhysicalRelease
            release = None
            if result.upc:
                stmt = select(PhysicalRelease).where(PhysicalRelease.upc == result.upc)
                release = (await session.execute(stmt)).scalar_one_or_none()

            if not release:
                release = PhysicalRelease(
                    movie_id=movie.id,
                    upc=result.upc,
                    bluray_com_id=result.bluray_com_id,
                    format=result.format,
                    label=result.label,
                    region=result.region,
                    physical_release_date=result.physical_release_date,
                    edition=result.edition,
                    set_name=result.set_name,
                    disc_count=result.disc_count,
                    aspect_ratio=result.aspect_ratio,
                    cover_url=result.cover_url,
                    cover_url_back=result.cover_url_back,
                )
                session.add(release)
            else:
                release.cover_url = result.cover_url or release.cover_url
                release.physical_release_date = result.physical_release_date or release.physical_release_date
                release.label = result.label or release.label

            await session.commit()
            console.print(f"\n[green]✓ Saved:[/green] {result.title} (DB id={release.id})")

    asyncio.run(_add())


# ── list ──────────────────────────────────────────────────────────────────────

@app.command(name="list")
def list_library(
    format_filter: Optional[str] = typer.Option(None, "--format", "-f", help="Filter by format"),
    label_filter: Optional[str] = typer.Option(None, "--label", "-l", help="Filter by label"),
):
    """List all releases in the library database."""
    from medialibrary.database import init_db, get_session, Movie, PhysicalRelease
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    async def _list():
        engine = await init_db()
        async with await get_session(engine) as session:
            stmt = select(PhysicalRelease).options(joinedload(PhysicalRelease.movie))
            if format_filter:
                stmt = stmt.where(PhysicalRelease.format.ilike(f"%{format_filter}%"))
            if label_filter:
                stmt = stmt.where(PhysicalRelease.label.ilike(f"%{label_filter}%"))
            releases = (await session.execute(stmt)).scalars().all()

        table = Table(title=f"Media Library ({len(releases)} titles)")
        table.add_column("Title", style="bold")
        table.add_column("Year", width=6)
        table.add_column("Director")
        table.add_column("Format", width=10)
        table.add_column("Label")
        table.add_column("Region", width=8)
        table.add_column("Rating", width=7)
        table.add_column("Runtime", width=9)

        for r in sorted(releases, key=lambda x: x.movie.title if x.movie else ""):
            m = r.movie
            table.add_row(
                m.title if m else "?",
                str(m.year) if m and m.year else "",
                m.director if m else "",
                r.format or "",
                r.label or "",
                r.region or "",
                m.mpaa_rating if m else "",
                f"{m.runtime_minutes}m" if m and m.runtime_minutes else "",
            )
        console.print(table)

    asyncio.run(_list())


# ── export ────────────────────────────────────────────────────────────────────

@app.command()
def export(
    output: Path = typer.Option(Path("library.csv"), "--output", "-o"),
    sheets_format: bool = typer.Option(True, "--sheets/--no-sheets",
                                        help="Include =IMAGE() formula for Google Sheets"),
):
    """Export library to CSV (importable into Google Sheets)."""
    from medialibrary.database import init_db, get_session, Movie, PhysicalRelease
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    async def _export():
        engine = await init_db()
        async with await get_session(engine) as session:
            stmt = select(PhysicalRelease).options(joinedload(PhysicalRelease.movie))
            releases = (await session.execute(stmt)).scalars().all()

        fieldnames = [
            "Film", "Box Art", "Year", "Director", "Format", "Label",
            "Region", "Set", "MPAA Rating", "Runtime (min)",
            "Physical Release Date", "Aspect Ratio", "UPC",
            "TMDB ID", "IMDb ID", "Blu-ray.com ID", "Cover URL",
        ]

        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in sorted(releases, key=lambda x: x.movie.title if x.movie else ""):
                m = r.movie
                cover = r.cover_url or ""
                box_art = f'=IMAGE("{cover}")' if sheets_format and cover else cover
                writer.writerow({
                    "Film": m.title if m else "",
                    "Box Art": box_art,
                    "Year": m.year if m else "",
                    "Director": m.director if m else "",
                    "Format": r.format or "",
                    "Label": r.label or "",
                    "Region": r.region or "",
                    "Set": r.set_name or "",
                    "MPAA Rating": m.mpaa_rating if m else "",
                    "Runtime (min)": m.runtime_minutes if m else "",
                    "Physical Release Date": r.physical_release_date or "",
                    "Aspect Ratio": r.aspect_ratio or "",
                    "UPC": r.upc or "",
                    "TMDB ID": m.tmdb_id if m else "",
                    "IMDb ID": m.imdb_id if m else "",
                    "Blu-ray.com ID": r.bluray_com_id or "",
                    "Cover URL": cover,
                })

        console.print(f"[green]✓ Exported {len(releases)} releases to {output}[/green]")

    asyncio.run(_export())


# ── helpers ───────────────────────────────────────────────────────────────────

def _print_result(result: EnrichedRelease) -> None:
    console.print(f"\n[bold cyan]{'─' * 60}[/bold cyan]")
    console.print(f"[bold]{result.title}[/bold] ({result.year})")
    console.print(f"[bold cyan]{'─' * 60}[/bold cyan]")

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="dim", width=22)
    table.add_column("Value")

    rows = [
        ("Director", result.director),
        ("Runtime", f"{result.runtime_minutes} min" if result.runtime_minutes else ""),
        ("MPAA Rating", result.mpaa_rating),
        ("Genres", result.genres),
        ("Format", result.format),
        ("Label", result.label),
        ("Region", result.region),
        ("Physical Release", result.physical_release_date),
        ("Edition", result.edition),
        ("Set", result.set_name),
        ("Disc Count", str(result.disc_count) if result.disc_count else ""),
        ("Aspect Ratio", result.aspect_ratio),
        ("UPC", result.upc),
        ("TMDB ID", str(result.tmdb_id) if result.tmdb_id else ""),
        ("IMDb ID", result.imdb_id),
        ("Blu-ray.com ID", str(result.bluray_com_id) if result.bluray_com_id else ""),
        ("Cover URL", result.cover_url or result.poster_url),
    ]
    for key, val in rows:
        if val:
            table.add_row(key, val)

    console.print(table)

    if result.overview:
        console.print(f"\n[dim]{result.overview[:200]}{'…' if len(result.overview) > 200 else ''}[/dim]")

    if result.warnings:
        console.print("\n[yellow]Warnings:[/yellow]")
        for w in result.warnings:
            console.print(f"  [yellow]⚠[/yellow] {w}")

    console.print(f"\n[dim]Sources: {', '.join(result.sources) or 'none'}[/dim]")


def _print_sheets_row(result: EnrichedRelease) -> None:
    """Print a tab-separated row ready to paste into Google Sheets."""
    cover = result.cover_url or result.poster_url
    formula = f'=IMAGE("{cover}")' if cover else ""
    row = [
        result.title,
        formula,
        str(result.year or ""),
        result.director,
        result.format,
        result.label,
        result.region,
        result.set_name,
    ]
    print("\t".join(row))


def main():
    app()


if __name__ == "__main__":
    main()
