import asyncio
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


# ============================================================
# Configuration
# ============================================================

URL = "https://www.teqsa.gov.au/"

# Number of pages downloaded simultaneously
MAX_CONCURRENT_REQUESTS = 50

# More generous timeouts for slower pages
TIMEOUT = httpx.Timeout(
    connect=20.0,
    read=30.0,
    write=20.0,
    pool=20.0,
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
# Check whether URL should be crawled
# ============================================================

def is_page_url(url):
    parsed = urlparse(url)

    # Only HTTP/HTTPS
    if parsed.scheme not in ("http", "https"):
        return False

    # Only our site
    if parsed.hostname != SITE_HOSTNAME:
        return False

    # Exclude files
    path = parsed.path.lower()

    if any(
        path.endswith(extension)
        for extension in EXCLUDED_EXTENSIONS
    ):
        return False

    return True


# ============================================================
# Extract links from HTML
# ============================================================

def extract_links(url, html):
    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    links = set()

    for link in soup.find_all("a", href=True):

        href = link["href"].strip()

        # Convert relative URL to absolute URL
        full_url = urljoin(
            url,
            href
        )

        # Remove URL fragments
        full_url = full_url.split("#", 1)[0]

        if is_page_url(full_url):
            links.add(full_url)

    return links


# ============================================================
# Fetch one page
# ============================================================

async def fetch_page(client, url):

    try:

        response = await client.get(url)

        response.raise_for_status()

        return (
            url,
            response,
            None
        )

    except httpx.HTTPError as error:

        return (
            url,
            None,
            error
        )


# ============================================================
# Main crawler
# ============================================================

async def crawl_site():

    limits = httpx.Limits(
        max_connections=MAX_CONCURRENT_REQUESTS,
        max_keepalive_connections=MAX_CONCURRENT_REQUESTS,
    )

    # ============================================================
    # Start crawler timer
    # ============================================================

    start_time = time.perf_counter()

    print()
    print(f"Starting crawl with {MAX_CONCURRENT_REQUESTS} workers...")


    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        limits=limits,
        follow_redirects=True,
        headers={
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
        },
    ) as client:

        # ====================================================
        # Fetch homepage
        # ====================================================

        print("Fetching homepage...")

        try:

            response = await client.get(URL)

            print(
                "Status:",
                response.status_code
            )

            print(
                "Content-Type:",
                response.headers.get("Content-Type")
            )

            response.raise_for_status()

        except httpx.HTTPError as error:

            print()
            print("Homepage request failed")
            print(
                "Exception type:",
                type(error).__name__
            )
            print(
                "Exception:",
                repr(error)
            )

            return

        # ====================================================
        # Extract homepage text
        # ====================================================

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # Remove things that aren't useful page text
        for element in soup([
            "script",
            "style",
            "noscript"
        ]):
            element.decompose()

        homepage_text = soup.get_text(
            " ",
            strip=True
        )

        # ====================================================
        # Save homepage text
        # ====================================================

        TEXT_FILE.write_text(
            homepage_text,
            encoding="utf-8"
        )

        print(
            f"Saved homepage text to: "
            f"{TEXT_FILE}"
        )

        # ====================================================
        # Extract links from homepage
        # ====================================================

        initial_links = extract_links(
            URL,
            response.text
        )

        print(
            f"Found {len(initial_links)} "
            f"initial links"
        )

        # ====================================================
        # Crawler state
        # ====================================================

        # URLs waiting to be crawled
        queue = set(initial_links)

        # URLs already crawled or currently being crawled
        visited = {URL}

        # Every page discovered
        all_links = set(initial_links)

        # ====================================================
        # Crawl site
        # ====================================================

        while queue:

            # ------------------------------------------------
            # Take a batch of URLs
            # ------------------------------------------------

            batch = list(queue)[
                :MAX_CONCURRENT_REQUESTS
            ]

            # Remove them from queue
            for url in batch:
                queue.remove(url)

            # Mark as visited before requesting them
            visited.update(batch)

            # ------------------------------------------------
            # Start concurrent requests
            # ------------------------------------------------

            tasks = [
                fetch_page(
                    client,
                    url
                )
                for url in batch
            ]

            results = await asyncio.gather(
                *tasks
            )

            # ------------------------------------------------
            # Process results
            # ------------------------------------------------

            for url, response, error in results:

                if error:

                    print(
                        f"ERROR: {url} | "
                        f"{type(error).__name__}"
                    )

                    continue

                # --------------------------------------------
                # Make sure this is HTML
                # --------------------------------------------

                content_type = response.headers.get(
                    "Content-Type",
                    ""
                ).lower()

                if "text/html" not in content_type:
                    continue

                # --------------------------------------------
                # Extract links
                # --------------------------------------------

                new_links = extract_links(
                    url,
                    response.text
                )

                # --------------------------------------------
                # Add newly discovered links
                # --------------------------------------------

                for link in new_links:

                    if link not in all_links:

                        all_links.add(link)

                        if link not in visited:
                            queue.add(link)

                print(
                    f"Crawled: {url} | "
                    f"Found {len(new_links)} links"
                )

            # ------------------------------------------------
            # Progress
            # ------------------------------------------------

            print(
                f"Progress: "
                f"Crawled={len(visited)} | "
                f"Queue={len(queue)} | "
                f"Total={len(all_links)}"
            )

            print()

        # ====================================================
        # Save all links
        # ====================================================

        LINKS_FILE.write_text(
            "\n".join(
                sorted(all_links)
            ),
            encoding="utf-8"
        )

        # ====================================================
        # Finished
        # ====================================================

        elapsed_time = time.perf_counter() - start_time

        print()
        print("--------------------------------")
        print("Crawl complete")
        print("--------------------------------")
        print(
            f"Saved homepage text: "
            f"{TEXT_FILE}"
        )
        print(
            f"Found {len(all_links)} pages"
        )
        print(
            f"Saved all links: "
            f"{LINKS_FILE}"
        )
        print(f"Pages found: {len(all_links)}")
        print(f"Time taken: {elapsed_time:.2f} seconds")
        print(f"Time taken: {elapsed_time / 60:.2f} minutes")
        print(f"Workers: {MAX_CONCURRENT_REQUESTS}")
        print(f"Pages per second: {len(visited) / elapsed_time:.2f}")


# ============================================================
# Run crawler
# ============================================================

if __name__ == "__main__":
    asyncio.run(
        crawl_site()
    )
