import asyncio
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from selectolax.lexbor import LexborHTMLParser


# ============================================================
# CONFIGURATION
# ============================================================

URL = "https://www.teqsa.gov.au/"


# ------------------------------------------------------------
# 1. NUMBER OF WORKERS
# ------------------------------------------------------------
#
# Controls how many requests can be in flight at once.
#
# Try:
#
#     1
#     3
#     5
#     10
#     20
#
# More workers does NOT necessarily mean faster.
#
MAX_WORKERS = 15


# ------------------------------------------------------------
# 2. REQUEST RATE
# ------------------------------------------------------------
#
# Maximum number of new requests started per second.
#
# Examples:
#
#     1.0  = 1 request/sec
#     2.0  = 2 requests/sec
#     5.0  = 5 requests/sec
#     10.0 = 10 requests/sec
#
# Set to None to disable rate limiting and let the workers
# run as fast as they can.
#
REQUESTS_PER_SECOND = 27.5


# ------------------------------------------------------------
# 3. HTTP TIMEOUT
# ------------------------------------------------------------

TIMEOUT = aiohttp.ClientTimeout(
    total=30,
    connect=20,
    sock_connect=20,
    sock_read=30,
)


# ------------------------------------------------------------
# 4. OUTPUT
# ------------------------------------------------------------

OUTPUT_DIR = Path("crawler_data")
OUTPUT_DIR.mkdir(exist_ok=True)

TEXT_FILE = OUTPUT_DIR / "page.txt"
LINKS_FILE = OUTPUT_DIR / "links.txt"


# ------------------------------------------------------------
# Site hostname
# ------------------------------------------------------------

SITE_HOSTNAME = urlparse(URL).hostname


# ============================================================
# FILE TYPES WE DON'T WANT TO CRAWL
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

    ".css",
    ".js",
    ".json",
    ".xml",
}


# ============================================================
# STATISTICS
# ============================================================

stats = {
    "requests": 0,
    "success": 0,
    "forbidden": 0,
    "not_found": 0,
    "rate_limited": 0,
    "server_errors": 0,
    "other_errors": 0,
}


# ============================================================
# GLOBAL REQUEST RATE LIMITER
# ============================================================

class RateLimiter:
    """
    Simple global request-rate limiter.

    If REQUESTS_PER_SECOND = 5:

        one request starts approximately every 0.2 seconds.

    All workers share the same limiter.
    """

    def __init__(self, requests_per_second):

        self.requests_per_second = requests_per_second

        self.lock = asyncio.Lock()

        self.next_request_time = 0.0

        if requests_per_second:
            self.interval = 1.0 / requests_per_second
        else:
            self.interval = 0.0


    async def wait(self):

        # Rate limiting disabled
        if not self.requests_per_second:
            return

        async with self.lock:

            now = time.perf_counter()

            # Work out when this request is allowed
            if now < self.next_request_time:

                wait_time = (
                    self.next_request_time - now
                )

                await asyncio.sleep(
                    wait_time
                )

            # Schedule the next request
            self.next_request_time = max(
                self.next_request_time,
                time.perf_counter(),
            ) + self.interval


# ============================================================
# URL FILTERING
# ============================================================

def is_page_url(url):

    parsed = urlparse(url)

    # HTTP/HTTPS only
    if parsed.scheme not in (
        "http",
        "https",
    ):
        return False

    # Same hostname only
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
# URL NORMALIZATION
# ============================================================

def normalize_url(url):

    # Remove #fragment
    url = url.split("#", 1)[0]

    # Remove trailing slash except for homepage
    parsed = urlparse(url)

    if (
        parsed.path != "/"
        and parsed.path.endswith("/")
    ):
        parsed = parsed._replace(
            path=parsed.path.rstrip("/")
        )

        url = parsed.geturl()

    return url


# ============================================================
# EXTRACT LINKS
# ============================================================

def extract_links(url, html):

    parser = LexborHTMLParser(html)

    links = set()

    for node in parser.css("a"):

        href = node.attributes.get("href")

        if not href:
            continue

        href = href.strip()

        if not href:
            continue

        # Convert relative URL to absolute URL
        full_url = urljoin(
            url,
            href
        )

        # Normalize
        full_url = normalize_url(
            full_url
        )

        if is_page_url(full_url):

            links.add(full_url)

    return links


# ============================================================
# EXTRACT TEXT
# ============================================================

def extract_text(html):

    parser = LexborHTMLParser(html)

    # Remove non-content elements
    for selector in (
        "script",
        "style",
        "noscript",
    ):

        for node in parser.css(selector):

            node.decompose()

    if parser.body:

        return parser.body.text(
            separator=" ",
            strip=True
        )

    return ""


# ============================================================
# FETCH ONE PAGE
# ============================================================

async def fetch_page(
    session,
    url,
    rate_limiter,
):

    # --------------------------------------------------------
    # Wait for global request rate limiter
    # --------------------------------------------------------

    await rate_limiter.wait()

    stats["requests"] += 1

    try:

        async with session.get(url) as response:

            status = response.status


            # =================================================
            # SUCCESS
            # =================================================

            if 200 <= status < 300:

                stats["success"] += 1

                content_type = response.headers.get(
                    "Content-Type",
                    ""
                ).lower()

                # We only want HTML
                if "text/html" not in content_type:

                    return (
                        url,
                        None,
                        None,
                    )

                html = await response.text(
                    errors="ignore"
                )

                return (
                    url,
                    html,
                    None,
                )


            # =================================================
            # 403
            # =================================================
            #
            # IMPORTANT:
            #
            # We DON'T assume 403 means rate limiting.
            #
            # Some pages may legitimately be inaccessible.
            #
            # We simply record the 403 and move on.
            # =================================================

            if status == 403:

                stats["forbidden"] += 1

                return (
                    url,
                    None,
                    "HTTP 403",
                )


            # =================================================
            # 404
            # =================================================

            if status == 404:

                stats["not_found"] += 1

                return (
                    url,
                    None,
                    "HTTP 404",
                )


            # =================================================
            # 429
            # =================================================
            #
            # This IS normally a strong rate-limit signal.
            # =================================================

            if status == 429:

                stats["rate_limited"] += 1

                retry_after = response.headers.get(
                    "Retry-After"
                )

                return (
                    url,
                    None,
                    f"HTTP 429 "
                    f"(Retry-After: {retry_after})",
                )


            # =================================================
            # 5xx
            # =================================================

            if 500 <= status < 600:

                stats["server_errors"] += 1

                return (
                    url,
                    None,
                    f"HTTP {status}",
                )


            # =================================================
            # Everything else
            # =================================================

            stats["other_errors"] += 1

            return (
                url,
                None,
                f"HTTP {status}",
            )


    except asyncio.TimeoutError:

        stats["other_errors"] += 1

        return (
            url,
            None,
            "Timeout",
        )


    except aiohttp.ClientError as error:

        stats["other_errors"] += 1

        return (
            url,
            None,
            type(error).__name__,
        )


    except Exception as error:

        stats["other_errors"] += 1

        return (
            url,
            None,
            f"{type(error).__name__}: {error}",
        )


# ============================================================
# WORKER
# ============================================================

async def worker(
    worker_id,
    queue,
    session,
    visited,
    all_links,
    rate_limiter,
):

    while True:

        url = await queue.get()

        try:

            # ------------------------------------------------
            # Fetch
            # ------------------------------------------------

            _, html, error = await fetch_page(
                session,
                url,
                rate_limiter,
            )


            # ------------------------------------------------
            # Error
            # ------------------------------------------------

            if error:

                print(
                    f"[Worker {worker_id}] "
                    f"{error}: "
                    f"{url}"
                )

                continue


            # ------------------------------------------------
            # Non-HTML
            # ------------------------------------------------

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
            # Add new URLs
            # ------------------------------------------------

            new_count = 0

            for link in new_links:

                if link in all_links:

                    continue

                all_links.add(link)

                new_count += 1

                if link not in visited:

                    visited.add(link)

                    await queue.put(
                        link
                    )


            # ------------------------------------------------
            # Progress
            # ------------------------------------------------

            print(
                f"[Worker {worker_id}] "
                f"Crawled: {url} | "
                f"Links: {len(new_links)} | "
                f"New: {new_count} | "
                f"Queue: {queue.qsize()} | "
                f"Total: {len(all_links)} | "
                f"403: {stats['forbidden']}"
            )


        finally:

            queue.task_done()


# ============================================================
# MAIN CRAWLER
# ============================================================

async def crawl_site():

    # --------------------------------------------------------
    # Reset statistics
    # --------------------------------------------------------

    for key in stats:

        stats[key] = 0


    # --------------------------------------------------------
    # Timer
    # --------------------------------------------------------

    start_time = time.perf_counter()


    print()
    print("================================")
    print("Async Site Crawler")
    print("================================")

    print(
        f"Site:              {URL}"
    )

    print(
        f"Workers:           {MAX_WORKERS}"
    )

    if REQUESTS_PER_SECOND:

        print(
            f"Request rate:      "
            f"{REQUESTS_PER_SECOND:.2f} req/sec"
        )

    else:

        print(
            "Request rate:      UNLIMITED"
        )

    print()


    # ========================================================
    # Rate limiter
    # ========================================================

    rate_limiter = RateLimiter(
        REQUESTS_PER_SECOND
    )


    # ========================================================
    # Connection pool
    # ========================================================

    connector = aiohttp.TCPConnector(

        limit=MAX_WORKERS,

        limit_per_host=MAX_WORKERS,

        ttl_dns_cache=300,

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

        "Accept-Language": (
            "en-US,en;q=0.9"
        ),
    }


    # ========================================================
    # HTTP session
    # ========================================================

    async with aiohttp.ClientSession(

        connector=connector,

        timeout=TIMEOUT,

        headers=headers,

    ) as session:


        # ====================================================
        # Homepage
        # ====================================================

        print(
            "Fetching homepage..."
        )


        homepage_url, homepage_html, error = await fetch_page(
            session,
            URL,
            rate_limiter,
        )


        if error:

            print(
                f"Homepage error: "
                f"{error}"
            )

            return


        if homepage_html is None:

            print(
                "Homepage did not return HTML."
            )

            return


        print(
            "Homepage downloaded."
        )


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
            f"Saved homepage: "
            f"{TEXT_FILE}"
        )


        # ====================================================
        # Discover homepage links
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

        visited = set()

        all_links = set()


        # Homepage
        visited.add(
            URL
        )


        # ====================================================
        # Add initial URLs
        # ====================================================

        for link in initial_links:

            if link not in visited:

                visited.add(link)

                all_links.add(link)

                await queue.put(
                    link
                )


        # ====================================================
        # Start workers
        # ====================================================

        workers = [

            asyncio.create_task(
                worker(
                    worker_id=i + 1,
                    queue=queue,
                    session=session,
                    visited=visited,
                    all_links=all_links,
                    rate_limiter=rate_limiter,
                )
            )

            for i in range(
                MAX_WORKERS
            )
        ]


        print()
        print(
            "Starting crawl..."
        )
        print()


        # ====================================================
        # Wait for everything in queue
        # ====================================================

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
        # Save URLs
        # ====================================================

        LINKS_FILE.write_text(

            "\n".join(
                sorted(all_links)
            ),

            encoding="utf-8"
        )


    # ========================================================
    # Timer
    # ========================================================

    elapsed = (
        time.perf_counter()
        - start_time
    )


    # ========================================================
    # Statistics
    # ========================================================

    total_pages = len(all_links)

    requests = stats["requests"]

    pages_per_second = (
        total_pages / elapsed
        if elapsed > 0
        else 0
    )

    requests_per_second = (
        requests / elapsed
        if elapsed > 0
        else 0
    )


    # ========================================================
    # Final report
    # ========================================================

    print()
    print("================================")
    print("CRAWL COMPLETE")
    print("================================")

    print(
        f"Pages discovered:  {total_pages:,}"
    )

    print(
        f"Requests made:     {requests:,}"
    )

    print(
        f"Workers:           {MAX_WORKERS}"
    )

    if REQUESTS_PER_SECOND:

        print(
            f"Configured rate:    "
            f"{REQUESTS_PER_SECOND:.2f}/sec"
        )

    else:

        print(
            "Configured rate:    unlimited"
        )

    print(
        f"Actual rate:        "
        f"{requests_per_second:.2f}/sec"
    )

    print(
        f"Time:               "
        f"{elapsed:.2f} seconds"
    )

    print(
        f"Time:               "
        f"{elapsed / 60:.2f} minutes"
    )

    print(
        f"Pages/sec:          "
        f"{pages_per_second:.2f}"
    )

    print()
    print("HTTP results")
    print("--------------------------------")

    print(
        f"Successful:         "
        f"{stats['success']:,}"
    )

    print(
        f"403 Forbidden:      "
        f"{stats['forbidden']:,}"
    )

    print(
        f"404 Not Found:      "
        f"{stats['not_found']:,}"
    )

    print(
        f"429 Rate Limited:   "
        f"{stats['rate_limited']:,}"
    )

    print(
        f"5xx Server errors:  "
        f"{stats['server_errors']:,}"
    )

    print(
        f"Other errors:       "
        f"{stats['other_errors']:,}"
    )

    print()
    print(
        f"Homepage:           "
        f"{TEXT_FILE}"
    )

    print(
        f"Links:              "
        f"{LINKS_FILE}"
    )

    print("================================")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        crawl_site()
    )
