from __future__ import annotations
from playwright.sync_api import Page, sync_playwright

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
SOUNDCLOUD_SC_A_ID = os.getenv("SOUNDCLOUD_SC_A_ID")
SOUNDCLOUD_USER_ID = os.getenv("SOUNDCLOUD_USER_ID")
SOUNDCLOUD_APP_VERSION = os.getenv("SOUNDCLOUD_APP_VERSION")
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


def search_soundcloud(page: Page, query: str) -> list[dict[str, str]]:
    """
    Search SoundCloud using an existing Playwright page.

    The browser/page is created once in main(), then reused for every query.
    This is much faster than launching Firefox separately for every search.
    """

    search_url = (
        f"https://soundcloud.com/search/sounds"
        f"?q={query}"
        f"&filter.created_at={CREATED_AT_FILTER}"
    )

    print(f"Searching SoundCloud page for query={query}, filter={CREATED_AT_FILTER}")

    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)

    # SoundCloud results are rendered by JavaScript, so give the page time to fill in.
    page.wait_for_timeout(8000)

    links = page.locator("a").evaluate_all(
        """
        (anchors, query) => anchors
            .map(a => ({
                text: a.innerText,
                href: a.href
            }))
            .filter(item =>
                item.href &&
                    item.href.includes("soundcloud.com") &&
                    item.text &&
                    item.text.toLowerCase().includes(query.toLowerCase())
            )
        """,
        query,
    )

    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for item in links:
        title = item.get("text", "").strip()
        url = item.get("href", "").strip()

        if not title or not url:
            continue
        if url in seen_urls:
            continue

        seen_urls.add(url)

        results.append(
            {
                "title": title,
                "url": url,
                "created_at": "",
                "username": "SoundCloud search result",
            }
        )

        if len(results) >= SOUNDCLOUD_LIMIT:
            break

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


def send_discord_alert(track: dict[str, str]) -> None:
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL in .env")

    # username = track.get("username", "Unknown uploader")
    # created_at = format_soundcloud_time(track.get("created_at", ""))

    payload = {
        "username": "SoundCloud Notifier",
        "content": track["url"],
    }

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)

    if response.status_code >= 400:
        print("Discord webhook failed.")
        print("Status:", response.status_code)
        print("Response:", response.text[:1000])
        print("Payload content length:", len(payload.get("content", "")))

    response.raise_for_status()


def main() -> None:
    print(
        f"SEARCH_BACKEND=playwright, "
        f"SOUNDCLOUD_SEARCH_QUERIES={SEARCH_QUERIES}, "
        f"SOUNDCLOUD_CREATED_AT_FILTER={CREATED_AT_FILTER}, "
        f"SOUNDCLOUD_LIMIT={SOUNDCLOUD_LIMIT}"
    )

    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL in .env")

    seen = load_seen_tracks()
    total_new_count = 0

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)

        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                "Gecko/20100101 Firefox/128.0"
            )
        )

        try:
            for query in SEARCH_QUERIES:
                results = search_soundcloud(page, query)

                print(
                    f"Found {len(results)} matching results before seen-filtering "
                    f"for query `{query}`."
                )

                if not results:
                    print(f"No matching SoundCloud results found for `{query}`.")
                    continue

                for track in reversed(results):
                    track_id = track["url"]

                    if track_id in seen:
                        print(
                            f"Already seen, skipping: "
                            f"{track['title']} - {track['url']}"
                        )
                        continue

                    print(
                        f"New track/result for `{query}`: "
                        f"{track['title']} - {track['url']}"
                    )

                    send_discord_alert(query, track)
                    seen.add(track_id)
                    total_new_count += 1

        finally:
            browser.close()

    save_seen_tracks(seen)

    print(f"Done. New SoundCloud alerts sent: {total_new_count}")


if __name__ == "__main__":
    main()
