from __future__ import annotations

import json
import os

from pathlib import Path

from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SEEN_FILE = BASE_DIR / "seen_soundcloud_tracks.json"

SEARCH_QUERIES = [
    query.strip()
    for query in os.getenv("SOUNDCLOUD_SEARCH_QUERIES", "babystaydown").split(",")
    if query.strip()
]
CREATED_AT_FILTER = os.getenv("SOUNDCLOUD_CREATED_AT_FILTER", "last_hour")
SOUNDCLOUD_CLIENT_ID = os.getenv("SOUNDCLOUD_CLIENT_ID")
SOUNDCLOUD_LIMIT = int(os.getenv("SOUNDCLOUD_LIMIT", 20))
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
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
    Search SoundCloud using the same api-v2 JSON endpoint the browser uses.
    """

    if not SOUNDCLOUD_CLIENT_ID:
        raise RuntimeError("Missing SOUNDCLOUD_CLIENT_ID in .env")

    url = "https://api-v2.soundcloud.com/search/tracks"

    params = {
        "q": query,
        "filter.created_at": CREATED_AT_FILTER,
        "facet": "genre",
        "client_id": SOUNDCLOUD_CLIENT_ID,
        "limit": SOUNDCLOUD_LIMIT,
        "offset": 0,
        "linked_partitioning": 1,
        "app_locale": "en",
    }

    print(f"Searching SoundCloud API for query={query}, filter={CREATED_AT_FILTER}")

    response = requests.get(url, headers=HEADERS, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()
    collection = data.get("collection", [])

    results: list[dict[str, str]] = []

    for track in collection:
        title = track.get("title") or ""
        permalink_url = track.get("permalink_url") or ""
        created_at = track.get("created_at") or ""
        username = ""

        user = track.get("user")
        if isinstance(user, dict):
            username = user.get("username") or ""

        if not permalink_url:
            continue

        normalized = f"{title} {permalink_url} {username}".lower()

        if query.lower() not in normalized:
            continue

        results.append(
            {
                "title": title,
                "url": permalink_url,
                "created_at": created_at,
                "username": username,
            }
        )

    return results


def format_soundcloud_time(created_at: str) -> str:
    if not created_at:
        return "Unknown"

    try:
        utc_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        local_dt = utc_dt.astimezone(ZoneInfo("America/New_York"))
        return local_dt.strftime("%m/%d/%y %-I:%M %p ET")
    except Exception:
        return created_at


def send_discord_alert(query: str, track: dict[str, str]) -> None:
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL in .env")

    username = track.get("username", "Unknown uploader")
    created_at = format_soundcloud_time(track.get("created_at", ""))

    payload = {
        "username": "SoundCloud Notifier",
        "content": (
            f"Uploader: {username}\nTime uploaded: {created_at}\n{track['url']}"
        ),
    }

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
    response.raise_for_status()


def main() -> None:
    print(
        f"Config loaded: "
        f"SOUNDCLOUD_SEARCH_QUERIES={SEARCH_QUERIES}, "
        f"SOUNDCLOUD_CREATED_AT_FILTER={CREATED_AT_FILTER}, "
        f"SOUNDCLOUD_LIMIT={SOUNDCLOUD_LIMIT}, "
        f"SOUNDCLOUD_CLIENT_ID_LOADED={bool(SOUNDCLOUD_CLIENT_ID)}"
    )
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL in .env")

    seen = load_seen_tracks()
    total_new_count = 0

    for query in SEARCH_QUERIES:
        results = search_soundcloud(query)

        print(
            f"Found {len(results)} matching results before seen-filtering for query `{query}`."
        )

        if not results:
            print(f"No matching SoundCloud results found for `{query}`.")
            continue

        for track in reversed(results):
            track_id = track["url"]

            if track_id in seen:
                print(f"Already seen, skipping: {track['title']} - {track['url']}")
                continue
            print(f"New track/result for `{query}`: {track['title']} - {track['url']}")
            send_discord_alert(query, track)
            seen.add(track_id)
            total_new_count += 1

    save_seen_tracks(seen)

    print(f"Done. New SoundCloud alerts sent: {total_new_count}")


if __name__ == "__main__":
    main()
