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


# ============================================================
# PERFORMANCE SETTINGS
# ============================================================

# Maximum number of requests that can be active at once.
#
# Try:
#
#     3
#     5
#     10
#     20
#     30
#
MAX_WORKERS = 40

# Set to None for unlimited.
#
REQUESTS_PER_SECOND = 50.0


# ============================================================
# BENCHMARK MODE
# ============================================================

# True:
#   Stop after BENCHMARK_PAGES successful HTML pages.
#
# False:
#   Crawl until there are no new pages.
#
BENCHMARK_MODE = True


# Number of successful pages to download during benchmark.
#
# 500 is a good starting point.
#
BENCHMARK_PAGES = 500


# How often to print live performance information.
#
# Example:
#
#     10 = print every 10 successful pages
#
PROGRESS_INTERVAL = 10


# ============================================================
# HTTP TIMEOUT
# ============================================================

TIMEOUT = aiohttp.ClientTimeout(
    total=30,
    connect=20,
    sock_connect=20,
    sock_read=30,
)


# ============================================================
# OUTPUT
# ============================================================

OUTPUT_DIR = Path("crawler_data")
OUTPUT_DIR.mkdir(exist_ok=True)

TEXT_FILE = OUTPUT_DIR / "page.txt"
LINKS_FILE = OUTPUT_DIR / "links.txt"


# ============================================================
# SITE
# ============================================================

SITE_HOSTNAME = urlparse(URL).hostname


# ============================================================
# FILE TYPES TO EXCLUDE
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
# RATE LIMITER
# ============================================================

class RateLimiter:

    def __init__(self, requests_per_second):

        self.requests_per_second = requests_per_second

        self.lock = asyncio.Lock()

        self.next_request_time = 0.0

        if requests_per_second:

            self.interval = (
                1.0 / requests_per_second
            )

        else:

            self.interval = 0.0


    async def wait(self):

        if not self.requests_per_second:

            return

        async with self.lock:

            now = time.perf_counter()

            if now < self.next_request_time:

                wait_time = (
                    self.next_request_time - now
                )

                await asyncio.sleep(
                    wait_time
                )

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

    # Same hostname
    if parsed.hostname != SITE_HOSTNAME:
        return False

    # Excluded files
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

    # Remove fragments
    url = url.split("#", 1)[0]

    parsed = urlparse(url)

    # Remove trailing slash except homepage
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

        href = node.attributes.get(
            "href"
        )

        if not href:
            continue

        href = href.strip()

        if not href:
            continue

        full_url = urljoin(
            url,
            href
        )

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
# FETCH PAGE
# ============================================================

async def fetch_page(
    session,
    url,
    rate_limiter,
):

    await rate_limiter.wait()

    stats["requests"] += 1

    try:

        async with session.get(url) as response:

            status = response.status


            # ------------------------------------------------
            # Successful response
            # ------------------------------------------------

            if 200 <= status < 300:

                content_type = response.headers.get(
                    "Content-Type",
                    ""
                ).lower()

                if "text/html" not in content_type:

                    return (
                        url,
                        None,
                        None,
                    )

                html = await response.text(
                    errors="ignore"
                )

                stats["success"] += 1

                return (
                    url,
                    html,
                    None,
                )


            # ------------------------------------------------
            # 403
            #
            # A 403 is NOT automatically considered rate
            # limiting. Some pages may simply be unauthorized.
            # ------------------------------------------------

            if status == 403:

                stats["forbidden"] += 1

                return (
                    url,
                    None,
                    "HTTP 403",
                )


            # ------------------------------------------------
            # 404
            # ------------------------------------------------

            if status == 404:

                stats["not_found"] += 1

                return (
                    url,
                    None,
                    "HTTP 404",
                )


            # ------------------------------------------------
            # 429
            # ------------------------------------------------

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


            # ------------------------------------------------
            # 5xx
            # ------------------------------------------------

            if 500 <= status < 600:

                stats["server_errors"] += 1

                return (
                    url,
                    None,
                    f"HTTP {status}",
                )


            # ------------------------------------------------
            # Other status
            # ------------------------------------------------

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
    stop_event,
    start_time,
):

    while True:

        # ----------------------------------------------------
        # Stop benchmark
        # ----------------------------------------------------

        if stop_event.is_set():

            return


        try:

            url = await asyncio.wait_for(
                queue.get(),
                timeout=1.0
            )

        except asyncio.TimeoutError:

            # Queue is empty.
            # Main crawler may still be discovering pages.
            continue


        try:

            # ------------------------------------------------
            # Check benchmark limit before request
            # ------------------------------------------------

            if (
                BENCHMARK_MODE
                and stats["success"] >= BENCHMARK_PAGES
            ):

                stop_event.set()

                continue


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

                # 403s are recorded but don't stop the crawl.
                #
                # They may simply represent pages the crawler
                # isn't authorized to access.

                if error == "HTTP 403":

                    print(
                        f"[Worker {worker_id}] "
                        f"403: {url}"
                    )

                elif error == "HTTP 404":

                    print(
                        f"[Worker {worker_id}] "
                        f"404: {url}"
                    )

                elif error.startswith("HTTP 429"):

                    print(
                        f"[Worker {worker_id}] "
                        f"RATE LIMITED: {url}"
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


            new_count = 0


            # ------------------------------------------------
            # Add new URLs
            # ------------------------------------------------

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
            # Live performance
            # ------------------------------------------------

            successful = stats["success"]

            if (
                successful > 0
                and successful % PROGRESS_INTERVAL == 0
            ):

                elapsed = (
                    time.perf_counter()
                    - start_time
                )

                pages_per_second = (
                    successful / elapsed
                )

                print(
                    ""
                )

                print(
                    "--------------------------------"
                )

                print(
                    f"Progress:          "
                    f"{successful:,}"
                )

                print(
                    f"Elapsed:           "
                    f"{elapsed:.1f}s"
                )

                print(
                    f"Successful/sec:    "
                    f"{pages_per_second:.2f}"
                )

                print(
                    f"Queue:             "
                    f"{queue.qsize():,}"
                )

                print(
                    f"Discovered:        "
                    f"{len(all_links):,}"
                )

                print(
                    f"403:               "
                    f"{stats['forbidden']:,}"
                )

                print(
                    f"429:               "
                    f"{stats['rate_limited']:,}"
                )

                print(
                    f"5xx:               "
                    f"{stats['server_errors']:,}"
                )

                print(
                    "--------------------------------"
                )

                print()


            # ------------------------------------------------
            # Benchmark complete
            # ------------------------------------------------

            if (
                BENCHMARK_MODE
                and stats["success"] >= BENCHMARK_PAGES
            ):

                stop_event.set()


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


    start_time = time.perf_counter()

    stop_event = asyncio.Event()


    # ========================================================
    # Header
    # ========================================================

    print()
    print("==========================================")
    print("ASYNC SITE CRAWLER")
    print("==========================================")

    print(
        f"Site:             {URL}"
    )

    print(
        f"Workers:          {MAX_WORKERS}"
    )

    if REQUESTS_PER_SECOND:

        print(
            f"Request rate:     "
            f"{REQUESTS_PER_SECOND:.2f}/sec"
        )

    else:

        print(
            "Request rate:     UNLIMITED"
        )


    if BENCHMARK_MODE:

        print(
            f"Benchmark:        "
            f"{BENCHMARK_PAGES:,} pages"
        )

    else:

        print(
            "Mode:             FULL CRAWL"
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
    # Headers
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

        "Accept-Language":
            "en-US,en;q=0.9",
    }


    # ========================================================
    # Session
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


        _, homepage_html, error = await fetch_page(
            session,
            URL,
            rate_limiter,
        )


        if error:

            print(
                f"Homepage error: {error}"
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


        # ====================================================
        # Initial links
        # ====================================================

        initial_links = extract_links(
            URL,
            homepage_html
        )


        print(
            f"Homepage links: "
            f"{len(initial_links):,}"
        )


        # ====================================================
        # Crawler state
        # ====================================================

        queue = asyncio.Queue()

        visited = set()

        all_links = set()


        visited.add(URL)


        for link in initial_links:

            if link not in visited:

                visited.add(link)

                all_links.add(link)

                await queue.put(link)


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

                    stop_event=stop_event,

                    start_time=start_time,

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
        # Benchmark mode
        # ====================================================

        if BENCHMARK_MODE:

            # Wait until benchmark reaches target.
            #
            # Workers set stop_event when the target is
            # reached.

            while not stop_event.is_set():

                await asyncio.sleep(
                    0.1
                )


        # ====================================================
        # Full crawl
        # ====================================================

        else:

            await queue.join()

            stop_event.set()


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
    # Final statistics
    # ========================================================

    elapsed = (
        time.perf_counter()
        - start_time
    )


    successful = stats["success"]

    successful_per_second = (

        successful / elapsed

        if elapsed > 0

        else 0

    )


    actual_request_rate = (

        stats["requests"] / elapsed

        if elapsed > 0

        else 0

    )


    # ========================================================
    # Results
    # ========================================================

    print()
    print()
    print("==========================================")
    print("RESULT")
    print("==========================================")


    print(
        f"Successful pages: "
        f"{successful:,}"
    )


    print(
        f"Discovered URLs:   "
        f"{len(all_links):,}"
    )


    print(
        f"Total requests:    "
        f"{stats['requests']:,}"
    )


    print(
        f"Workers:           "
        f"{MAX_WORKERS}"
    )


    if REQUESTS_PER_SECOND:

        print(
            f"Configured rate:   "
            f"{REQUESTS_PER_SECOND:.2f}/sec"
        )

    else:

        print(
            "Configured rate:   "
            "UNLIMITED"
        )


    print(
        f"Actual rate:       "
        f"{actual_request_rate:.2f}/sec"
    )


    print(
        f"Successful/sec:    "
        f"{successful_per_second:.2f}"
    )


    print(
        f"Time:              "
        f"{elapsed:.2f} seconds"
    )


    print(
        f"Time:              "
        f"{elapsed / 60:.2f} minutes"
    )


    print()
    print("HTTP results")
    print("------------------------------------------")


    print(
        f"Successful:        "
        f"{stats['success']:,}"
    )


    print(
        f"403 Forbidden:     "
        f"{stats['forbidden']:,}"
    )


    print(
        f"404 Not Found:     "
        f"{stats['not_found']:,}"
    )


    print(
        f"429 Rate Limited:  "
        f"{stats['rate_limited']:,}"
    )


    print(
        f"5xx errors:        "
        f"{stats['server_errors']:,}"
    )


    print(
        f"Other errors:      "
        f"{stats['other_errors']:,}"
    )


    print()
    print(
        f"Homepage text:     "
        f"{TEXT_FILE}"
    )


    print(
        f"Links:             "
        f"{LINKS_FILE}"
    )


    print("==========================================")
    print()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        crawl_site()
    )
