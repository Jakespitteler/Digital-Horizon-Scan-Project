import asyncio
import json
import logging
import tempfile
from time import perf_counter

import httpx

from src.web_scraper.site_crawler import crawl_site

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"  # noqa: E501
}


# URL: str = "https://quotes.toscrape.com"
URL: str = "https://www.teqsa.gov.au/"


async def main() -> set[str]:
    """Runs crawl site function on a real website"""
    async with httpx.AsyncClient(headers=HEADERS, timeout=20.0) as client:
        return await crawl_site(client, URL, max_pages=10000, max_concurrent=10, delay=1)


if __name__ == "__main__":
    start: float = perf_counter()
    links: set[str] = asyncio.run(main())
    logger.info(f"Time taken: {perf_counter() - start:.2f} seconds.")

    with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as temp_json:
        json.dump({URL: list(links)}, temp_json, indent=4)
        temp_json.flush()
        logger.info(f"Results saved to: {temp_json.name}")
