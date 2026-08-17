import requests
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import deque


URL = "https://www.teqsa.gov.au/"

OUTPUT_DIR = Path("crawler_data")
OUTPUT_DIR.mkdir(exist_ok=True)


def fetch_html(url):
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response


def extract_page(url, html):
    soup = BeautifulSoup(html, "html.parser")

    for element in soup(["script", "style", "noscript"]):
        element.decompose()

    text = soup.get_text(" ", strip=True)

    site_hostname = urlparse(URL).hostname

    links = set()

    for link in soup.find_all("a", href=True):
        href = link["href"].strip()

        full_url = urljoin(url, href)

        full_url = full_url.split("#")[0]

        parsed_url = urlparse(full_url)

        if (
            parsed_url.scheme in ("http", "https")
            and parsed_url.hostname == site_hostname
        ):
            links.add(full_url)

    return text, links

response = fetch_html(URL)

print("Status:", response.status_code)
print("Content-Type:", response.headers.get("Content-Type"))

text, links = extract_page(URL, response.text)

text_file = OUTPUT_DIR / "page.txt"

text_file.write_text(
    text,
    encoding="utf-8"
)
#crawl rest
queue = deque(links)
visited = {URL}
all_links = set(links)

while queue:
    url = queue.popleft()
    if url in visited:
        continue
    print(f"Crawling: {url}")
    try:
        response = fetch_html(url)

    except requests.RequestException as error:
        print(f"  ERROR: {error}")
        visited.add(url)
        continue

    visited.add(url)
    _, new_links = extract_page(url, response.text)

    print(f"  Found {len(new_links)} links")

    for link in new_links:

        if link not in all_links:
            all_links.add(link)
            queue.append(link)

    print(f"Crawled: {len(visited)} | Queue: {len(queue)}")


links_file = OUTPUT_DIR / "links.txt"

links_file.write_text(
    "\n".join(sorted(all_links)),
    encoding="utf-8"
)

print()
print(f"Saved homepage text to: {text_file}")
print(f"Found {len(all_links)} pages across the site")
print(f"Saved all links to: {links_file}")
