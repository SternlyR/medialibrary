#!/usr/bin/env python3
"""Debug script: fetch a Blu-ray.com page and show how films are listed."""
import asyncio, re, sys
sys.path.insert(0, '/home/sean/medialibrary')

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

BLURAY_ID = 394187
URL = f"https://www.blu-ray.com/movies/_/{BLURAY_ID}/"

async def main():
    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        r = await client.get(URL)
        print(f"Status: {r.status_code}")

    soup = BeautifulSoup(r.text, "html.parser")

    # Search for any element containing the film titles
    keywords = ["wicked go to hell", "nude in a white car", "taste of violence"]
    print("\n=== Elements containing film titles ===")
    for kw in keywords:
        for el in soup.find_all(string=re.compile(kw, re.IGNORECASE)):
            parent = el.parent
            print(f"\nKeyword: '{kw}'")
            print(f"  Tag: <{parent.name}> class={parent.get('class')} id={parent.get('id')}")
            print(f"  Text: {parent.get_text(strip=True)[:200]}")
            gp = parent.parent
            if gp:
                print(f"  Parent tag: <{gp.name}> class={gp.get('class')} id={gp.get('id')}")

    # Print all subheadings
    print("\n=== All <span class=subheading> headings ===")
    for h in soup.select("span.subheading"):
        print(f"  class={h.get('class')}  text='{h.get_text(strip=True)}'")

    # Print all h2/h3
    print("\n=== h2/h3 elements ===")
    for h in soup.select("h2, h3"):
        print(f"  <{h.name}>: '{h.get_text(strip=True)[:100]}'")

    # Look for any div/section with id or class containing 'title' or 'film' or 'set'
    print("\n=== Divs/sections with 'title'/'film'/'set' in class/id ===")
    for el in soup.find_all(True):
        cls = " ".join(el.get("class") or [])
        eid = el.get("id") or ""
        if any(kw in (cls + eid).lower() for kw in ["title", "film", "boxset", "set", "collection"]):
            txt = el.get_text(strip=True)[:150]
            if txt:
                print(f"  <{el.name}> class='{cls}' id='{eid}': {txt}")

asyncio.run(main())
