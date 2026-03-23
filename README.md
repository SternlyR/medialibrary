# Media Library

Automated metadata enrichment for your physical disc collection (Blu-ray, UHD, DVD).

## What it does

- **Scan a UPC barcode** → automatically fetches title, director, MPAA rating, runtime, physical release date, label, region, cover art
- **Search by title** → same metadata, no barcode needed
- **Google Sheets export** → drop-in replacement for your current manual sheet, with `=IMAGE()` formulas pre-filled
- **REST API** → foundation for a future mobile visual browser app

## Quick start

```bash
# 1. Install
pip install -e .

# 2. Configure (only TMDB_API_KEY is required — free at themoviedb.org)
cp .env.example .env
# Edit .env and add your TMDB_API_KEY

# 3. Look up a disc by UPC (scan barcode with phone, type it in)
medialibrary lookup --upc 025192251498

# 4. Look up by title
medialibrary lookup --title "Alien" --year 1979

# 5. Add to library database
medialibrary add --upc 025192251498

# 6. Export to CSV for Google Sheets
medialibrary export --output library.csv
```

## Data sources

| Source | What it provides | Key needed? |
|--------|-----------------|-------------|
| [TMDB](https://www.themoviedb.org/) | Director, MPAA rating, runtime, genres, poster | Yes (free) |
| [UPCitemdb](https://www.upcitemdb.com/) | UPC → title mapping | No (100/day free) |
| [Blu-ray.com](https://www.blu-ray.com/) | Physical release date, label, region, cover art, edition | No (scraped) |

## CLI reference

```
medialibrary lookup --upc <barcode>          # Look up without saving
medialibrary lookup --title "Title" --year N
medialibrary lookup --bluray-id <id>          # From blu-ray.com URL ?id=XXXXX
medialibrary lookup --tmdb-id <id>
medialibrary lookup --title "Alien" --sheets  # Tab-separated row for Sheets paste

medialibrary add --upc <barcode>             # Look up + save to DB
medialibrary add --title "Alien" --set "Alien Anthology"

medialibrary list                            # Show all library items
medialibrary list --format UHD
medialibrary list --label Criterion

medialibrary export                          # Export library.csv for Google Sheets
medialibrary export --output my_library.csv
```

## REST API (for the future mobile app)

```bash
uvicorn medialibrary.server:app --reload
# → http://localhost:8000
# → http://localhost:8000/docs  (interactive Swagger UI)
```

Key endpoints:

```
POST /releases/lookup    # Preview metadata before adding
POST /releases           # Add a release
GET  /releases           # List all (supports ?q=, ?format=, ?label=)
GET  /releases/{id}      # Single release
DELETE /releases/{id}    # Remove
GET  /export/csv         # Download CSV for Google Sheets
```

## Google Sheets column mapping

The exported CSV maps directly to your existing sheet:

| Sheet Column | Source |
|---|---|
| Film | TMDB title |
| Box Art | `=IMAGE("https://images.static-bluray.com/...")` |
| Year | TMDB release year |
| Director | TMDB credits |
| Format | Blu-ray.com / UPC |
| Label | Blu-ray.com |
| Region | Blu-ray.com (normalized: US, A, B, C, Free) |
| Set | Manual input |
| MPAA Rating | TMDB US certification |
| Runtime | TMDB (min) |
| Physical Release Date | Blu-ray.com |

## Backfilling your existing library

For titles already in your sheet, the fastest path:

1. Run `medialibrary add --title "Film Title" --year YYYY` for each
2. Or if you have UPCs: `medialibrary add --upc <barcode>`
3. Export with `medialibrary export` and import to Sheets

For bulk backfill, use the API with a simple script:
```python
import httpx, asyncio

titles = [("After Hours", 1985), ("Alien", 1979), ...]  # from your sheet

async def backfill():
    async with httpx.AsyncClient() as client:
        for title, year in titles:
            await client.post("http://localhost:8000/releases",
                              json={"title": title, "year": year})
            await asyncio.sleep(2)  # be nice to blu-ray.com

asyncio.run(backfill())
```
