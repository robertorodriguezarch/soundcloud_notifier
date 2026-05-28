from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import quote_plus


import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SEEN_FILE = BASE_DIR / "seen_soundcloud_tracks.json"

SEARCH_QUERY = os.getenv("SOUNDCLOUD_SEARCH_QUERY", "babystaydown")
CREATED_AT_FILTER = os.getenv("SOUNDCLOUD_CREATED_AT_FILTER", "last_hour")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64: rv:128.0) Gecko/20100101 Firefox/128.0"
    )
}


def load_seen_tracks() -> set[str]:
    if not SEEN_FILE.exists():
        return set()

    try:
        data = json.loads(SEEN_FILE.read_text())
        return {str(item) for item in data}
    except Exception:
        return set()


def save_seen_tracks(seen: set[str]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2))


def search_soundcloud(query: str) -> list[dict[str, str]]:
    """
    Public SoundCloud search scrape.

    First version:
    - searches SoundCloud web results
    - extracts track-looking links
    - filters links/titles containing the query
    """

    url = (
        f"https://soundcloud.com/search/sounds"
        f"?q={quote_plus(query)}"
        f"&filter.created_at={quote_plus(CREATED_AT_FILTER)}"
    )

    print(f"Searching SoundCloud: {url}")

    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = str(link["href"])

        # Skip non-track/search/navigation links
        if not href.startswith("/"):
            continue

        if href.startswith(("/search", "/discover", "/stream", "/you", "upload")):
            continue

        # Track URLs usually look like /artist/track-title
        parts = [part for part in href.split("/") if part]
        if len(parts) != 2:
            continue

        full_url = f"https://soundcloud.com{href}"
        title = link.get_text(" ", strip=True)

        # Some links have empty text; use URL slug as fallback
        if not title:
            title = parts[-1].replace("-", " ")

        normalized = f"{title} {href}".lower()
        if query.lower() not in normalized:
            continue

        if full_url in seen_urls:
            continue

        seen_urls.add(full_url)

        results.append(
            {
                "title": title,
                "url": full_url,
            }
        )

        return results


def send_discord_alert(track: dict[str, str]) -> None:
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL in .env")

    payload = {
        "username": "SoundCloud Notifier",
        "content": f"New SoundCloud result for `{SEARCH_QUERY}`:\n{track['title']}\n{track['url']}",
    }

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
    response.raise_for_status()


def main() -> None:
    print(
        f"Config loaded: "
        f"SOUNDCLOUD_SEARCH_QUERY={SEARCH_QUERY}, "
        f"SOUNDCLOUD_CREATED_AT_FILTER={CREATED_AT_FILTER}"
    )
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL in .env")

    seen = load_seen_tracks()
    results = search_soundcloud(SEARCH_QUERY)

    print(f"Found {len(results)} matching results before seen-filtering.")

    if not results:
        print("No matching SoundCloud results found.")
        return

    new_count = 0

    # Oldest-first-ish based on page order reversed.
    # We  can refine ordering later once we inspect actual results.
    for track in reversed(results):
        track_id = track["url"]

        if track_id in seen:
            print(f"Already seen, skipping: {track['title']} - {track['url']}")
            continue

        print(f"New track/result: {track['title']} - {track['url']}")
        send_discord_alert(track)
        seen.add(track_id)
        new_count += 1

    save_seen_tracks(seen)

    print(f"Done. New SoundCloud alerts sent: {new_count}")


if __name__ == "__main__":
    main()
