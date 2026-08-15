import logging
from asyncio import Semaphore, gather
from collections.abc import Awaitable, Iterator

from httpx import AsyncClient, HTTPStatusError, RequestError

from src.utils.html import extract_links, fetch_content_from_url

logger = logging.getLogger(__name__)


async def fetch_and_extract(
    client: AsyncClient,
    url: str,
    base_url: str,
    semaphore: Semaphore,
) -> tuple[str, list[str]]:
    """Safely fetches HTML and extracts internal links under a concurrency limit.

    Args:
        client (AsyncClient): the web client
        url (str): the url to fetch and extract internal links from
        base_url (str): The base url for resolving relative urls.
        semaphore (Semaphore): The concurrency limiter

    Returns:
        tuple[str, list[str]]: (the url, all of the internal links on that page)
    """
    async with semaphore:
        try:
            html: str = await fetch_content_from_url(client, url)  # TODO: remove logging every extraction
            links: list[str] = extract_links(html, base_url, internal_only=True)
            return url, links
        except (HTTPStatusError, RequestError, Exception) as e:
            # Gracefully ignore failing requests (404, 500, timeouts, bad HTML)
            logger.warning(f"{url=} could not be reached. Raised: {e}")
            return url, []


async def crawl_site(
    client: AsyncClient,
    base_url: str,
    max_pages: int = 5000,
    max_concurrent: int = 10,
) -> set[str]:
    """Crawls a website asynchronously starting from base_url up to max_pages.

    Args:
        client (httpx.AsyncClient): The web client
        base_url (str): The website for the crawler to crawl
        max_pages (int, optional): The limit on pages able to be visited before the crawler stops. Defaults to 5000.
        max_concurrent (int, optional): The maximum amount of urls to visit concurrently. Defaults to 10.

    Returns:
        set[str]: All internal links in the website
    """
    semaphore = Semaphore(max_concurrent)

    visited: set[str] = set()
    queued: set[str] = {base_url}
    queue: list[str] = [base_url]

    while queue and len(visited) < max_pages:
        # TODO: log batches
        batch_size: int = min(len(queue), max_pages - len(visited))
        batch: list[str] = queue[:batch_size]
        queue = queue[batch_size:]

        tasks: Iterator[Awaitable[tuple[str, list[str]]]] = (
            fetch_and_extract(client, url, base_url, semaphore) for url in batch
        )
        batch_results: list[tuple[str, list[str]]] = await gather(*tasks)

        for url, new_links in batch_results:
            visited.add(url)
            for link in new_links:
                if link not in visited and link not in queued:
                    queued.add(link)
                    queue.append(link)

    if len(visited) >= max_pages:
        logger.warning(f"Crawler exceeded the {max_pages=}, stopping crawler.")

    return visited
