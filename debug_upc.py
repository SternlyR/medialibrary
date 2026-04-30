"""Diagnostic script — run each enrichment step separately for a UPC."""
import asyncio, sys, json

async def main(upc: str):
    from medialibrary.api.upc import UPCClient
    from medialibrary.api.bluray import BlurayClient
    from medialibrary.api.tmdb import TMDBClient

    upc_client = UPCClient()
    bluray = BlurayClient()
    tmdb = TMDBClient()

    print(f"\n{'='*60}")
    print(f"STEP 1 — UPCitemdb: {upc}")
    print('='*60)
    upc_data = await upc_client.lookup(upc)
    print(json.dumps(upc_data, indent=2))

    print(f"\n{'='*60}")
    print(f"STEP 1.5 — Blu-ray.com UPC search: {upc}")
    print('='*60)
    candidates = await bluray.search(upc)
    print(f"Search stubs ({len(candidates)}):")
    for c in candidates:
        print(f"  id={c['bluray_com_id']}  year={c['year']}  title={c['title']}")
        print(f"  url={c.get('detail_url','')}")
    bluray_data = None
    if candidates:
        stub = candidates[0]
        print(f"\nFetching detail page for id={stub['bluray_com_id']} ...")
        bluray_data = await bluray.get_release(stub["bluray_com_id"])
        if bluray_data and stub.get("year"):
            bluray_data["film_year"] = stub["year"]
        print(json.dumps(bluray_data, indent=2, default=str))
    else:
        print("  NO RESULTS from Blu-ray.com UPC search")

    year_for_tmdb = (bluray_data or {}).get("film_year") if bluray_data else None
    title_for_tmdb = upc_data.get("search_title") or upc_data.get("raw_title") if upc_data else None

    print(f"\n{'='*60}")
    print(f"STEP 2 — TMDB: title={title_for_tmdb!r}  year={year_for_tmdb}")
    print('='*60)
    import re
    if title_for_tmdb:
        clean = re.sub(r'\s*\b(4K|UHD|Ultra\s*HD|Blu[- ]?ray|BD)\b.*$', '', title_for_tmdb, flags=re.IGNORECASE).strip() or title_for_tmdb
        print(f"Cleaned title for TMDB: {clean!r}")

        print(f"\nSearch with year={year_for_tmdb}:")
        r1 = await tmdb.search_movie(clean, year_for_tmdb)
        for m in r1[:3]:
            print(f"  [{m.get('id')}] {m.get('title')} ({m.get('release_date','')[:4]}) popularity={m.get('popularity')}")

        if not r1:
            print("  No results. Retrying without year...")
            r2 = await tmdb.search_movie(clean, None)
            for m in r2[:3]:
                print(f"  [{m.get('id')}] {m.get('title')} ({m.get('release_date','')[:4]}) popularity={m.get('popularity')}")

asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "715515277419"))
