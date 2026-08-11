from urllib.parse import ParseResult, urljoin, urlparse

from bs4 import BeautifulSoup
from httpx import AsyncClient, Response


async def fetch_content_from_url(client: AsyncClient, url: str) -> str:
    """Fetches raw HTML content from a given URL asynchronously.

    Args:
        client (httpx.AsyncClient): The HTTPX asynchronous client instance used for the request.
        url (str): The target URL to fetch content from.

    Returns:
        str: The raw HTML text content of the HTTP response.

    Raises:
        httpx.HTTPStatusError: If the HTTP response returns an unsuccessful status code (4xx or 5xx).
        httpx.RequestError: If a network connection error, timeout, or request failure occurs.

    """
    response: Response = await client.get(url)
    response.raise_for_status()
    return response.text


def extract_links(
    html_content: str,
    base_url: str,
    internal_only: bool = False,
) -> list[str]:
    """Extracts, resolves, and normalises unique links from HTML content.

    Args:
        html_content (str): The raw HTML string to be parsed for links.
        base_url (str): The base URL of the page, used to resolve relative paths.
        internal_only (bool): If True, filters links to only those sharing the base domain.

    Returns:
        list[str]: A sorted list of unique, absolute URLs found in the HTML matching criteria.

    Raises:
        ValueError: If the base_url is improperly formatted and cannot be parsed correctly.
        TypeError: If an href attribute is not a string.
    """
    parsed_base: ParseResult = urlparse(base_url)
    if not parsed_base.netloc:
        raise ValueError(f"Invalid base_url provided: {base_url}")

    soup = BeautifulSoup(html_content, "html.parser")
    links: set[str] = set()

    for tag in soup.find_all("a", href=True):
        href: str = str(tag["href"]).strip()

        # Skip non-navigational or empty links
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        # Resolve relative links into absolute URLs
        absolute_url: str = urljoin(base_url, href)
        parsed_url: ParseResult = urlparse(absolute_url)

        # Filter by internal domain if requested
        if internal_only and parsed_url.netloc != parsed_base.netloc:
            continue

        # Strip URL fragments (anchors like #section) for deduplication
        clean_url: str = parsed_url._replace(fragment="").geturl()
        links.add(clean_url)

    return sorted(links)
