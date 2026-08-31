import asyncio
import sys
from pathlib import Path

import httpx2


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from app.backend.web_scraper.site_crawler import crawl_site  # noqa: E402
from app.db.core import Base, engine, get_db_session  # noqa: E402
from app.db.services.website_service import WebsiteService  # noqa: E402
from src.utils.compare_links import compare_and_update_links  # noqa: E402


URL = "https://www.teqsa.gov.au/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Use a small number while testing.
MAX_PAGES = 10_000
MAX_CONCURRENT = 30
SECONDS_DELAY = 0.5
SECONDS_TIMEOUT = 20
BATCH_403_THRESHOLD = 30


async def run_crawler() -> set[str]:
    """Crawl the website and return all discovered links."""
    async with httpx2.AsyncClient(
        headers=HEADERS,
        timeout=SECONDS_TIMEOUT,
    ) as client:
        return await crawl_site(
            client,
            URL,
            max_pages=MAX_PAGES,
            max_concurrent=MAX_CONCURRENT,
            delay=SECONDS_DELAY,
            batch_403_threshold=BATCH_403_THRESHOLD,
        )


def test_crawler_saves_links_to_database() -> None:
    """Crawl a real website and store its links in the database."""
    Base.metadata.create_all(bind=engine)

    links = asyncio.run(run_crawler())

    assert links, f"The crawler returned no links for {URL}"

    current_data = {
        URL: sorted(links),
    }

    with get_db_session() as session:
        changes = compare_and_update_links(
            current_data=current_data,
            session=session,
        )

    assert URL in changes

    # Open a new session after commit and verify database contents.
    with get_db_session() as session:
        stored_website = WebsiteService(session).get_by_url(URL)
        stored_links = {
            internal_link.url
            for internal_link in stored_website.internal_links
        }

    assert stored_links == links

    print(f"Processed: {len(links)} links")
    print(f"Added: {len(changes[URL]['added'])}")
    print(f"Removed: {len(changes[URL]['removed'])}")


if __name__ == "__main__":
    test_crawler_saves_links_to_database()