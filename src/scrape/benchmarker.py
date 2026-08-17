import asyncio
import statistics
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from selectolax.lexbor import LexborHTMLParser


# ============================================================
# SITE
# ============================================================

URL = "https://www.teqsa.gov.au/"

SITE_HOSTNAME = urlparse(URL).hostname


# ============================================================
# BENCHMARK SETTINGS
# ============================================================

# Number of successful HTML pages per test.
#
# 500  = fast rough test
# 2000 = recommended
# 5000 = final confirmation
#
BENCHMARK_PAGES = 1000


# Number of times to repeat each configuration.
#
# 2 = faster
# 3 = recommended
# 5 = more reliable
#
RUNS_PER_CONFIGURATION = 2


# ============================================================
# STAGE 1
#
# Find the best number of workers while keeping the request
# rate fixed.
# ============================================================

WORKER_TESTS = [
    5, 10, 15, 20
]


FIXED_REQUEST_RATE = 30.0


# ============================================================
# STAGE 2
#
# Find the best request rate while keeping the winning worker
# count fixed.
# ============================================================

REQUEST_RATE_TESTS = [
    35.0,
    37.5
]


# ============================================================
# TIMEOUT
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
# RATE LIMITER
# ============================================================

class RateLimiter:

    def __init__(self, requests_per_second):

        self.requests_per_second = (
            requests_per_second
        )

        self.interval = (
            1.0 / requests_per_second
        )

        self.lock = asyncio.Lock()

        self.next_request_time = (
            time.perf_counter()
        )


    async def wait(self):

        async with self.lock:

            now = time.perf_counter()

            if now < self.next_request_time:

                await asyncio.sleep(
                    self.next_request_time - now
                )

            self.next_request_time = (
                max(
                    self.next_request_time,
                    time.perf_counter(),
                )
                + self.interval
            )


# ============================================================
# URL FILTERING
# ============================================================

def is_page_url(url):

    parsed = urlparse(url)

    if parsed.scheme not in (
        "http",
        "https",
    ):
        return False

    if parsed.hostname != SITE_HOSTNAME:
        return False

    path = parsed.path.lower()

    if any(
        path.endswith(extension)
        for extension in EXCLUDED_EXTENSIONS
    ):
        return False

    return True


# ============================================================
# NORMALIZE URL
# ============================================================

def normalize_url(url):

    # Remove fragment
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

        href = node.attributes.get("href")

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
    stats,
):

    await rate_limiter.wait()

    stats["requests"] += 1

    try:

        async with session.get(url) as response:

            status = response.status


            # ------------------------------------------------
            # Successful HTML page
            # ------------------------------------------------

            if 200 <= status < 300:

                content_type = (
                    response.headers.get(
                        "Content-Type",
                        ""
                    ).lower()
                )

                if "text/html" not in content_type:

                    stats["non_html"] += 1

                    return None, None

                html = await response.text(
                    errors="ignore"
                )

                stats["success"] += 1

                return html, None


            # ------------------------------------------------
            # 403
            # ------------------------------------------------

            if status == 403:

                stats["403"] += 1

                return None, "403"


            # ------------------------------------------------
            # 404
            # ------------------------------------------------

            if status == 404:

                stats["404"] += 1

                return None, "404"


            # ------------------------------------------------
            # 429
            # ------------------------------------------------

            if status == 429:

                stats["429"] += 1

                return None, "429"


            # ------------------------------------------------
            # 5xx
            # ------------------------------------------------

            if 500 <= status < 600:

                stats["5xx"] += 1

                return None, str(status)


            stats["other"] += 1

            return None, str(status)


    except asyncio.TimeoutError:

        stats["timeouts"] += 1

        return None, "timeout"


    except aiohttp.ClientError:

        stats["errors"] += 1

        return None, "client_error"


    except Exception:

        stats["errors"] += 1

        return None, "error"


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
    stats,
    stop_event,
):

    while not stop_event.is_set():

        try:

            url = await asyncio.wait_for(
                queue.get(),
                timeout=0.5,
            )

        except asyncio.TimeoutError:

            continue


        try:

            # Stop if benchmark already finished
            if stop_event.is_set():
                continue


            html, error = await fetch_page(
                session,
                url,
                rate_limiter,
                stats,
            )


            if html is None:
                continue


            # ------------------------------------------------
            # Extract links
            # ------------------------------------------------

            new_links = extract_links(
                url,
                html,
            )


            for link in new_links:

                if link in all_links:
                    continue

                all_links.add(link)

                if link not in visited:

                    visited.add(link)

                    await queue.put(link)


            # ------------------------------------------------
            # Check benchmark target
            # ------------------------------------------------

            if (
                stats["success"]
                >= BENCHMARK_PAGES
            ):

                stop_event.set()


        finally:

            queue.task_done()


# ============================================================
# RUN ONE BENCHMARK
# ============================================================

async def benchmark(
    workers_count,
    request_rate,
):

    stats = {
        "requests": 0,
        "success": 0,
        "403": 0,
        "404": 0,
        "429": 0,
        "5xx": 0,
        "non_html": 0,
        "other": 0,
        "timeouts": 0,
        "errors": 0,
    }


    start_time = time.perf_counter()


    # ========================================================
    # Connection pool
    # ========================================================

    connector = aiohttp.TCPConnector(

        limit=workers_count,

        limit_per_host=workers_count,

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


    rate_limiter = RateLimiter(
        request_rate
    )


    async with aiohttp.ClientSession(

        connector=connector,

        timeout=TIMEOUT,

        headers=headers,

    ) as session:


        # ====================================================
        # Homepage
        # ====================================================

        homepage_html, error = await fetch_page(
            session,
            URL,
            rate_limiter,
            stats,
        )


        if homepage_html is None:

            print(
                f"Homepage failed: {error}"
            )

            return None


        # ====================================================
        # Save homepage text only once
        # ====================================================

        if not TEXT_FILE.exists():

            homepage_text = extract_text(
                homepage_html
            )

            TEXT_FILE.write_text(
                homepage_text,
                encoding="utf-8",
            )


        # ====================================================
        # Initial links
        # ====================================================

        initial_links = extract_links(
            URL,
            homepage_html,
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
        # Stop event
        # ====================================================

        stop_event = asyncio.Event()


        # ====================================================
        # Workers
        # ====================================================

        worker_tasks = [

            asyncio.create_task(

                worker(

                    worker_id=i + 1,

                    queue=queue,

                    session=session,

                    visited=visited,

                    all_links=all_links,

                    rate_limiter=rate_limiter,

                    stats=stats,

                    stop_event=stop_event,

                )

            )

            for i in range(
                workers_count
            )
        ]


        # ====================================================
        # Wait for benchmark target
        # ====================================================

        while not stop_event.is_set():

            await asyncio.sleep(
                0.05
            )


        # ====================================================
        # Stop workers
        # ========================================================

        for task in worker_tasks:

            task.cancel()


        await asyncio.gather(
            *worker_tasks,
            return_exceptions=True,
        )


    # ========================================================
    # Results
    # ========================================================

    elapsed = (
        time.perf_counter()
        - start_time
    )


    successful_per_second = (
        stats["success"] / elapsed
    )


    actual_request_rate = (
        stats["requests"] / elapsed
    )


    return {
        "workers": workers_count,

        "request_rate": request_rate,

        "success": stats["success"],

        "requests": stats["requests"],

        "pages_per_second":
            successful_per_second,

        "actual_request_rate":
            actual_request_rate,

        "403": stats["403"],

        "404": stats["404"],

        "429": stats["429"],

        "5xx": stats["5xx"],

        "timeouts":
            stats["timeouts"],

        "errors":
            stats["errors"],

        "discovered":
            len(all_links),

        "elapsed":
            elapsed,
    }


# ============================================================
# RUN CONFIGURATION MULTIPLE TIMES
# ============================================================

async def test_configuration(
    workers,
    request_rate,
):

    results = []


    for run in range(
        1,
        RUNS_PER_CONFIGURATION + 1,
    ):

        print()
        print(
            "=========================================="
        )

        print(
            f"Workers:       {workers}"
        )

        print(
            f"Request rate:  {request_rate:.1f}/sec"
        )

        print(
            f"Run:           "
            f"{run}/{RUNS_PER_CONFIGURATION}"
        )

        print(
            f"Pages:         "
            f"{BENCHMARK_PAGES}"
        )

        print(
            "=========================================="
        )


        result = await benchmark(
            workers,
            request_rate,
        )


        if result is None:

            print(
                "Benchmark failed."
            )

            continue


        results.append(result)


        print()
        print(
            f"Successful/sec: "
            f"{result['pages_per_second']:.2f}"
        )

        print(
            f"Actual req/sec: "
            f"{result['actual_request_rate']:.2f}"
        )

        print(
            f"403:            "
            f"{result['403']}"
        )

        print(
            f"429:            "
            f"{result['429']}"
        )

        print(
            f"5xx:            "
            f"{result['5xx']}"
        )

        print(
            f"Timeouts:       "
            f"{result['timeouts']}"
        )

        print(
            f"Time:           "
            f"{result['elapsed']:.2f}s"
        )


    return results


# ============================================================
# CALCULATE SUMMARY
# ============================================================

def summarize(
    workers,
    request_rate,
    results,
):

    if not results:

        return None


    speeds = [
        result["pages_per_second"]
        for result in results
    ]


    median_speed = statistics.median(
        speeds
    )


    average_speed = statistics.mean(
        speeds
    )


    minimum_speed = min(
        speeds
    )


    maximum_speed = max(
        speeds
    )


    return {
        "workers": workers,

        "request_rate":
            request_rate,

        "median":
            median_speed,

        "average":
            average_speed,

        "minimum":
            minimum_speed,

        "maximum":
            maximum_speed,

        "runs":
            len(results),
    }


# ============================================================
# PRINT RANKING
# ============================================================

def print_ranking(
    title,
    summaries,
):

    print()
    print()
    print(
        "=========================================="
    )

    print(title)

    print(
        "=========================================="
    )


    sorted_results = sorted(
        summaries,
        key=lambda x: x["median"],
        reverse=True,
    )


    print(
        f"{'Workers':>8} "
        f"{'Req/s':>8} "
        f"{'Median':>10} "
        f"{'Average':>10} "
        f"{'Min':>10} "
        f"{'Max':>10}"
    )


    print(
        "-" * 62
    )


    for result in sorted_results:

        print(
            f"{result['workers']:>8} "
            f"{result['request_rate']:>8.1f} "
            f"{result['median']:>10.2f} "
            f"{result['average']:>10.2f} "
            f"{result['minimum']:>10.2f} "
            f"{result['maximum']:>10.2f}"
        )


    print()


    winner = sorted_results[0]


    print(
        f"BEST: "
        f"{winner['workers']} workers, "
        f"{winner['request_rate']:.1f} req/sec"
    )

    print(
        f"Median: "
        f"{winner['median']:.2f} pages/sec"
    )


    return winner


# ============================================================
# MAIN OPTIMIZER
# ============================================================

async def optimize():

    total_start = time.perf_counter()


    print()
    print(
        "##########################################"
    )

    print(
        "ASYNC CRAWLER PERFORMANCE OPTIMIZER"
    )

    print(
        "##########################################"
    )

    print()

    print(
        f"Benchmark pages: "
        f"{BENCHMARK_PAGES}"
    )

    print(
        f"Runs/configuration: "
        f"{RUNS_PER_CONFIGURATION}"
    )

    print()

    print(
        "Stage 1:"
    )

    print(
        f"Finding best workers at "
        f"{FIXED_REQUEST_RATE:.1f} req/sec"
    )


    # ========================================================
    # STAGE 1
    # ========================================================

    worker_summaries = []


    for workers in WORKER_TESTS:

        results = await test_configuration(
            workers,
            FIXED_REQUEST_RATE,
        )


        summary = summarize(
            workers,
            FIXED_REQUEST_RATE,
            results,
        )


        if summary:

            worker_summaries.append(
                summary
            )


    if not worker_summaries:

        print(
            "No successful worker tests."
        )

        return


    best_workers = print_ranking(
        "STAGE 1 — WORKER RESULTS",
        worker_summaries,
    )


    winning_workers = (
        best_workers["workers"]
    )


    # ========================================================
    # STAGE 2
    # ========================================================

    print()
    print(
        "Stage 2:"
    )

    print(
        f"Finding best request rate "
        f"using {winning_workers} workers"
    )


    rate_summaries = []


    for request_rate in REQUEST_RATE_TESTS:

        results = await test_configuration(
            winning_workers,
            request_rate,
        )


        summary = summarize(
            winning_workers,
            request_rate,
            results,
        )


        if summary:

            rate_summaries.append(
                summary
            )


    if not rate_summaries:

        print(
            "No successful rate tests."
        )

        return


    best_rate = print_ranking(
        "STAGE 2 — REQUEST RATE RESULTS",
        rate_summaries,
    )


    # ========================================================
    # FINAL RESULT
    # ========================================================

    elapsed_total = (
        time.perf_counter()
        - total_start
    )


    print()
    print()
    print(
        "##########################################"
    )

    print(
        "OPTIMAL CONFIGURATION"
    )

    print(
        "##########################################"
    )

    print()


    print(
        f"Workers:           "
        f"{best_rate['workers']}"
    )

    print(
        f"Request rate:      "
        f"{best_rate['request_rate']:.1f}/sec"
    )

    print(
        f"Median pages/sec:  "
        f"{best_rate['median']:.2f}"
    )

    print(
        f"Average pages/sec: "
        f"{best_rate['average']:.2f}"
    )

    print(
        f"Best run:          "
        f"{best_rate['maximum']:.2f}"
    )

    print(
        f"Worst run:         "
        f"{best_rate['minimum']:.2f}"
    )

    print()


    print(
        "Use this as your starting configuration:"
    )

    print()

    print(
        f"MAX_WORKERS = "
        f"{best_rate['workers']}"
    )

    print(
        f"REQUESTS_PER_SECOND = "
        f"{best_rate['request_rate']}"
    )


    print()

    print(
        f"Total benchmark time: "
        f"{elapsed_total / 60:.2f} minutes"
    )

    print()

    print(
        "NOTE:"
    )

    print(
        "The result is optimized for this site's "
        "current response behaviour."
    )

    print(
        "Run the winning configuration again with "
        "5,000+ pages before using it for a full crawl."
    )

    print(
        "##########################################"
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        optimize()
    )
