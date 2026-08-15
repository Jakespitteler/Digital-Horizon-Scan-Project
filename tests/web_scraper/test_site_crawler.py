import json

import pytest
from httpx import AsyncClient

from src.web_scraper.site_crawler import crawl_site

# BASE_URL: str = "https://quotes.toscrape.com"  # TODO: Investigate skipped pages

BASE_URL: str = "https://www.teqsa.gov.au/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
MAX_PAGES: int = 100


@pytest.mark.anyio
async def test_crawl_site() -> None:

    async with AsyncClient(headers=HEADERS, timeout=20.0) as client:
        links: set[str] = await crawl_site(client, BASE_URL, max_pages=MAX_PAGES, max_concurrent=3)

    with open("internal_links.json", "w") as file:
        json.dump({BASE_URL: list(links)}, file, indent=4)

    assert len(links) == MAX_PAGES  # NOT A GOOD TEST
