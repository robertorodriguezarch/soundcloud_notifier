from __future__ import annotations
from playwright.sync_api import Page, sync_playwright
from urllib.parse import quote_plus, urlparse

import json
import os
import time

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


def is_soundcloud_track_url(url: str) -> bool:
    parsed = urlparse(url)

    if parsed.netloc not in {"soundcloud.com", "www.soundcloud.com"}:
        return False

    parts = [part for part in parsed.path.split("/") if part]

    # Track URLs usually look like /artist-or-user/track-title
    if len(parts) != 2:
        return False

    blocked_first_parts = {
        "search",
        "discover",
        "charts",
        "pages",
        "tags",
        "you",
        "getstarted",
        "company",
    }

    if parts[0] in blocked_first_parts:
        return False

    return True


def search_soundcloud(page: Page, query: str) -> list[dict[str, str]]:
    """
    Search SoundCloud using an existing Playwright page.

    The browser/page is created once in main(), then reused for every query.
    This is much faster than launching Firefox separately for every search.
    """

    encoded_query = quote_plus(query)

    search_url = (
        f"https://soundcloud.com/search/sounds"
        f"?q={encoded_query}"
        f"&filter.created_at={CREATED_AT_FILTER}"
    )

    print(f"Searching SoundCloud page for query={query}, filter={CREATED_AT_FILTER}")

    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)

    # SoundCloud results are rendered by JavaScript, so give the page time to fill in.
    page.wait_for_timeout(8000)

    # Scroll to force more lazy-loaded search results to appear.
    for _ in range(4):
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(2000)

    links = page.locator("a").evaluate_all("""
        anchors => anchors.map(a => ({
        text: a.innerText || "",
        href: a.href || ""
        }))
        """)

    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    query_lower = query.lower()

    print(f"Extracted {len(links)} total anchor links for `{query}`.")

    for item in links:
        raw_title = item.get("text", "").strip()
        url = item.get("href", "").strip()

        if not url:
            continue

        url_lower = url.lower()
        title_lower = raw_title.lower()

        if not is_soundcloud_track_url(url):
            continue

        if query_lower not in title_lower and query_lower not in url_lower:
            continue

        if any(
            blocked in url_lower
            for blocked in [
                "/search/",
                "/search?",
                "/comments" "/tags/",
                "/pages/",
                "/charts/",
                "/discover/",
                "/you/",
                "help.soundcloud.com",
            ]
        ):
            continue

        if query_lower not in title_lower and query_lower not in url_lower:
            continue

        if url in seen_urls:
            continue

        seen_urls.add(url)

        title = raw_title or url.rstrip("/").split("/")[-1].replace("-", " ")

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


def send_discord_alert(query: str, track: dict[str, str]) -> None:
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL in .env")

    payload = {
        "username": "SoundCloud Notifier",
        "content": (f"`{query}`:\n" f"**{track['title']}**\n" f"{track['url']}"),
    }

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)

    if response.status_code == 429:
        try:
            retry_after = response.json().get("retry_after", 1)
        except Exception:
            retry_after = 1

        print(f"Discord rate limited. Sleeping for {retry_after} seconds.")
        time.sleep(float(retry_after) + 0.5)

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

        try:
            for query in SEARCH_QUERIES:
                page = browser.new_page(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64; rv: 128.0) "
                        "Gecko/20100101 Firefox/128.0"
                    )
                )

                try:
                    results = search_soundcloud(page, query)
                finally:
                    page.close()

                print(
                    f"Found {len(results)} matching results before seen-filtering "
                    f"for query `{query}`."
                )

                if not results:
                    print(f"No matching SoundCloud results found for `{query}`.")
                    continue

                for track in reversed(results):
                    track_id = track["url"]

                    print(
                        f"Candidate result for `{query}`: "
                        f"{track['title']} - {track['url']}"
                    )

                    if track_id in seen:
                        print(
                            f"Already seen, skipping: "
                            f"{track['title']} - {track['url']}"
                        )
                        continue

                    print(
                        f"New track/result for `{query}`: "
                        f"{track['title']} = {track['url']}"
                    )

                    send_discord_alert(query, track)
                    seen.add(track_id)
                    save_seen_tracks(seen)
                    total_new_count += 1
                    time.sleep(1)
        finally:
            browser.close()

    save_seen_tracks(seen)

    print(f"Done. New SoundCloud alerts sent: {total_new_count}")


if __name__ == "__main__":
    main()
