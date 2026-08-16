import logging
from pathlib import PurePosixPath
from urllib.parse import ParseResult, urljoin, urlparse

from bs4 import BeautifulSoup
from httpx import AsyncClient, Response

logging.getLogger("httpx").setLevel(logging.WARNING)

WEB_PAGE_EXTENSIONS = {"", ".html", ".htm", ".php", ".asp", ".aspx", ".jsp"}


def _is_web_page(url: str) -> bool:
    """Determines if a link goes to a web page (rather than a document).

    Args:
        url (str): The url to check.

    Returns:
        bool: True if the url goes to a webpage.
    """
    path: str = urlparse(url).path
    suffix: str = PurePosixPath(path).suffix.lower()
    return suffix in WEB_PAGE_EXTENSIONS


def normalise_url(url: str) -> str:
    """Normalises a URL by removing fragments and trailing slashes for deduplication.

    Args:
        url (str): The url to normalise.

    Returns:
        str: The normalised url.
    """
    parsed: ParseResult = urlparse(url)
    path = parsed.path
    path = "/" if not path or path == "/" else path.rstrip("/")
    return parsed._replace(fragment="", path=path).geturl()


async def fetch_content_from_url(client: AsyncClient, url: str) -> tuple[str, str]:
    """Fetches raw HTML content from a given URL asynchronously. Follows redirects to the url that is returned is

    Args:
        client (httpx.AsyncClient): The HTTPX asynchronous client instance used for the request.
        url (str): The target URL to fetch content from.

    Returns:
        tuple[str, str]: The raw HTML text content and the final URL (after any redirects).

    Raises:
        httpx.HTTPStatusError: If the HTTP response returns an unsuccessful status code (4xx or 5xx).
        httpx.RequestError: If a network connection error, timeout, or request failure occurs.

    """
    response: Response = await client.get(url, follow_redirects=True)
    response.raise_for_status()
    return response.text, str(response.url)


def extract_links(
    url: str,
    html_content: str,
    internal_only: bool = False,
) -> list[str]:
    """Extracts, resolves, and normalises unique links from HTML content.

    Args:
        url (str): The URL of the page, used to resolve relative paths.
        html_content (str): The raw HTML string to be parsed for links.
        internal_only (bool): If True, filters links to only those sharing the base domain.

    Returns:
        list[str]: A sorted list of unique, absolute URLs found in the HTML matching criteria.

    Raises:
        ValueError: If the url is improperly formatted and cannot be parsed correctly.
        TypeError: If an href attribute is not a string.
    """
    parsed_base: ParseResult = urlparse(url)
    if not parsed_base.netloc:
        raise ValueError(f"Invalid url provided: {url}")

    links: set[str] = set()
    for tag in BeautifulSoup(html_content, "html.parser").find_all("a", href=True):
        href: str = str(tag["href"]).strip()

        # Skip non-navigational or empty links
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        # Resolve relative links into absolute URLs
        absolute_url: str = urljoin(url, href)

        # Filter by internal domain if requested
        if internal_only and (not _is_web_page(absolute_url) or urlparse(absolute_url).netloc != parsed_base.netloc):
            continue

        links.add(normalise_url(absolute_url))

    return sorted(links)
