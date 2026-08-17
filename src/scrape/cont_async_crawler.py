import asyncio
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from selectolax.lexbor import LexborHTMLParser


# ============================================================
# Configuration
# ============================================================

URL = "https://www.teqsa.gov.au/"

# Number of pages being downloaded/processed at once
MAX_CONCURRENT_REQUESTS = 3

# HTTP timeout
TIMEOUT = aiohttp.ClientTimeout(
    total=30,
    connect=20,
    sock_connect=20,
    sock_read=30,
)

OUTPUT_DIR = Path("crawler_data")
OUTPUT_DIR.mkdir(exist_ok=True)

TEXT_FILE = OUTPUT_DIR / "page.txt"
LINKS_FILE = OUTPUT_DIR / "links.txt"

SITE_HOSTNAME = urlparse(URL).hostname


# ============================================================
# File types we do NOT want to crawl
# ============================================================

EXCLUDED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
}


# ============================================================
# URL filtering
# ============================================================

def is_page_url(url):
    """
    Return True if the URL is an HTML page on our site.
    """

    parsed = urlparse(url)

    # HTTP/HTTPS only
    if parsed.scheme not in ("http", "https"):
        return False

    # Same site only
    if parsed.hostname != SITE_HOSTNAME:
        return False

    # Exclude documents/media
    path = parsed.path.lower()

    if any(
        path.endswith(extension)
        for extension in EXCLUDED_EXTENSIONS
    ):
        return False

    return True


# ============================================================
# Extract links using Selectolax
# ============================================================

def extract_links(url, html):
    """
    Extract all same-site page links from HTML.
    """

    parser = LexborHTMLParser(html)

    links = set()

    for node in parser.css("a"):

        href = node.attributes.get("href")

        if not href:
            continue

        href = href.strip()

        if not href:
            continue

        # Convert relative URLs to absolute URLs
        full_url = urljoin(
            url,
            href
        )

        # Remove #fragment
        full_url = full_url.split("#", 1)[0]

        if is_page_url(full_url):
            links.add(full_url)

    return links


# ============================================================
# Extract text from homepage
# ============================================================

def extract_text(html):
    """
    Extract visible-ish text from HTML.

    This is only used for the homepage at the moment.
    """

    parser = LexborHTMLParser(html)

    # Remove things that aren't useful page text
    for selector in (
        "script",
        "style",
        "noscript",
    ):
        for node in parser.css(selector):
            node.decompose()

    return parser.body.text(
        separator=" ",
        strip=True
    )


# ============================================================
# Fetch one page
# ============================================================

async def fetch_page(session, url):
    """
    Download one page.

    Returns:
        url, html, error
    """

    try:

        async with session.get(url) as response:

            # We only care about successful responses
            if response.status >= 400:

                return (
                    url,
                    None,
                    f"HTTP {response.status}"
                )

            # Check content type
            content_type = response.headers.get(
                "Content-Type",
                ""
            ).lower()

            # We don't want PDFs/files/etc.
            if "text/html" not in content_type:

                return (
                    url,
                    None,
                    None
                )

            # Read response
            html = await response.text(
                errors="ignore"
            )

            return (
                url,
                html,
                None
            )

    except asyncio.TimeoutError:

        return (
            url,
            None,
            "Timeout"
        )

    except aiohttp.ClientError as error:

        return (
            url,
            None,
            type(error).__name__
        )

    except Exception as error:

        return (
            url,
            None,
            f"{type(error).__name__}: {error}"
        )


# ============================================================
# Worker
# ============================================================

async def worker(
    worker_id,
    queue,
    session,
    visited,
    all_links,
):
    """
    Continuously take URLs from the queue and crawl them.

    Unlike a batch system, a worker immediately gets another
    URL as soon as it finishes its current page.
    """

    while True:

        url = await queue.get()

        try:

            # ------------------------------------------------
            # Download page
            # ------------------------------------------------

            _, html, error = await fetch_page(
                session,
                url
            )

            if error:

                print(
                    f"ERROR: {url} | {error}"
                )

                continue

            if html is None:
                continue

            # ------------------------------------------------
            # Extract links
            # ------------------------------------------------

            new_links = extract_links(
                url,
                html
            )

            # ------------------------------------------------
            # Add newly discovered URLs
            # ------------------------------------------------

            new_count = 0

            for link in new_links:

                if link not in all_links:

                    all_links.add(link)
                    new_count += 1

                    # ------------------------------------------------
                    # Important:
                    #
                    # Add to visited BEFORE putting into the queue.
                    #
                    # This prevents multiple pages from adding the
                    # same URL to the queue at the same time.
                    # ------------------------------------------------

                    if link not in visited:

                        visited.add(link)

                        await queue.put(link)

            # ------------------------------------------------
            # Progress
            # ------------------------------------------------

            print(
                f"[Worker {worker_id}] "
                f"Crawled: {url} | "
                f"Links: {len(new_links)} | "
                f"New: {new_count} | "
                f"Queue: {queue.qsize()} | "
                f"Total: {len(all_links)}"
            )

        finally:

            queue.task_done()


# ============================================================
# Main crawler
# ============================================================

async def crawl_site():

    # ========================================================
    # Start timer
    # ========================================================

    start_time = time.perf_counter()

    print()
    print("================================")
    print("Async site crawler")
    print("================================")
    print(f"Site: {URL}")
    print(
        f"Workers: "
        f"{MAX_CONCURRENT_REQUESTS}"
    )
    print()


    # ========================================================
    # HTTP connection pool
    # ========================================================

    connector = aiohttp.TCPConnector(

        # Maximum simultaneous connections
        limit=MAX_CONCURRENT_REQUESTS,

        # Maximum connections to this particular site
        limit_per_host=MAX_CONCURRENT_REQUESTS,

        # Keep DNS results cached
        ttl_dns_cache=300,

        # Enable connection reuse
        force_close=False,
    )


    # ========================================================
    # HTTP headers
    # ========================================================

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),

        "Accept": (
            "text/html,"
            "application/xhtml+xml,"
            "application/xml;q=0.9,"
            "*/*;q=0.8"
        ),

        "Accept-Language": "en-US,en;q=0.9",
    }


    # ========================================================
    # Create HTTP session
    # ========================================================

    async with aiohttp.ClientSession(

        connector=connector,
        timeout=TIMEOUT,
        headers=headers,

    ) as session:


        # ====================================================
        # Fetch homepage
        # ====================================================

        print("Fetching homepage...")

        homepage_url, homepage_html, error = await fetch_page(
            session,
            URL
        )

        if error:

            print()
            print("Homepage error:")
            print(error)
            return

        if homepage_html is None:

            print()
            print("Homepage did not return HTML.")
            return


        print("Homepage downloaded successfully.")


        # ====================================================
        # Save homepage text
        # ====================================================

        homepage_text = extract_text(
            homepage_html
        )

        TEXT_FILE.write_text(
            homepage_text,
            encoding="utf-8"
        )

        print(
            f"Saved homepage text: "
            f"{TEXT_FILE}"
        )


        # ====================================================
        # Extract homepage links
        # ====================================================

        initial_links = extract_links(
            URL,
            homepage_html
        )

        print(
            f"Homepage links: "
            f"{len(initial_links)}"
        )


        # ====================================================
        # Crawler state
        # ====================================================

        queue = asyncio.Queue()

        # URL has been discovered/queued
        visited = set()

        # Every page discovered
        all_links = set()


        # ----------------------------------------------------
        # Add homepage
        # ----------------------------------------------------

        visited.add(URL)


        # ----------------------------------------------------
        # Add initial links
        # ----------------------------------------------------

        for link in initial_links:

            if link not in visited:

                visited.add(link)
                all_links.add(link)

                await queue.put(link)


        # ====================================================
        # Create workers
        # ====================================================

        workers = [

            asyncio.create_task(
                worker(
                    worker_id=i + 1,
                    queue=queue,
                    session=session,
                    visited=visited,
                    all_links=all_links,
                )
            )

            for i in range(
                MAX_CONCURRENT_REQUESTS
            )
        ]


        # ====================================================
        # Wait until all URLs are crawled
        # ====================================================

        print()
        print("Starting continuous crawl...")
        print()

        await queue.join()


        # ====================================================
        # Stop workers
        # ====================================================

        for task in workers:

            task.cancel()

        await asyncio.gather(
            *workers,
            return_exceptions=True
        )


        # ====================================================
        # Save links
        # ====================================================

        LINKS_FILE.write_text(

            "\n".join(
                sorted(all_links)
            ),

            encoding="utf-8"
        )


    # ========================================================
    # Stop timer
    # ========================================================

    elapsed_time = (
        time.perf_counter()
        - start_time
    )


    # ========================================================
    # Statistics
    # ========================================================

    total_pages = len(all_links)

    pages_per_second = (
        total_pages / elapsed_time
        if elapsed_time > 0
        else 0
    )


    # ========================================================
    # Finished
    # ========================================================

    print()
    print("================================")
    print("Crawl complete")
    print("================================")

    print(
        f"Pages found:       {total_pages}"
    )

    print(
        f"Workers:           "
        f"{MAX_CONCURRENT_REQUESTS}"
    )

    print(
        f"Time taken:        "
        f"{elapsed_time:.2f} seconds"
    )

    print(
        f"Time taken:        "
        f"{elapsed_time / 60:.2f} minutes"
    )

    print(
        f"Pages/second:      "
        f"{pages_per_second:.2f}"
    )

    print(
        f"Saved homepage:    "
        f"{TEXT_FILE}"
    )

    print(
        f"Saved links:       "
        f"{LINKS_FILE}"
    )

    print("================================")


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        crawl_site()
    )