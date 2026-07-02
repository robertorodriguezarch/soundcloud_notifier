from __future__ import annotations

from playwright.sync_api import sync_playwright


def main() -> None:
    query = "pradabagshawty"
    url = f"https://soundcloud.com/search/sounds?q={query}&filter.created_at=last_day"

    with sync_playwright() as p:
        browser = p.firefox.launch(
            headless=True,
        )

        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
            )
        )

        print(f"Opening: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        print("Waiting for SoundCloud results...")
        page.wait_for_timeout(10000)

        links = page.locator("a").evaluate_all(
            """
            anchors => anchors
                .map(a => ({
                    text: a.innerText,
                    href: a.href
                }))
                .filter(item =>
                    item.href &&
                    item.href.includes("soundcloud.com") &&
                    item.text &&
                    item.text.toLowerCase().includes("pradabagshawty")
                )
            """
        )

        print(f"Found {len(links)} matching links.")

        for item in links[:20]:
            print("---")
            print("Text:", item["text"])
            print("URL:", item["href"])

        browser.close()


if __name__ == "__main__":
    main()
