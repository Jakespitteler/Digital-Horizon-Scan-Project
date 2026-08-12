from collections.abc import Callable

import pytest
from httpx import AsyncClient, ConnectError, HTTPStatusError, MockTransport, Request, RequestError, Response

from src.utils.html import extract_links, fetch_content_from_url

TEST_URL: str = "https://example.com"
SAMPLE_HTML: str = """
<html>
    <body>
        <a href="/about">About Us</a>
        <a href="https://example.com/contact">Contact</a>
        <a href="https://external.com/page">External Site</a>
        <a href="#top">Back to Top</a>
        <a href="/about">Duplicate About</a>
        <a href="javascript:void(0)">JS Link</a>
        <a href="mailto:test@example.com">Email</a>
    </body>
</html>
"""


def _make_handler(
    status_code: int = 200,
    text: str = "",
    error_class: type[RequestError] | None = None,
) -> Callable[[Request], Response]:
    """Factory helper to generate a mock request handler."""

    def handle_request(request: Request) -> Response:
        if error_class:
            raise error_class("Simulated network error", request=request)
        return Response(status_code, text=text)

    return handle_request


@pytest.mark.anyio
async def test_fetch_content_from_url_success() -> None:
    """Test successful HTML content retrieval."""
    async with AsyncClient(transport=MockTransport(_make_handler(200, SAMPLE_HTML))) as client:
        content = await fetch_content_from_url(client, url=TEST_URL)
    assert content == SAMPLE_HTML


@pytest.mark.anyio
async def test_fetch_content_from_url_http_error() -> None:
    """Test that HTTP status errors raise correctly through the mock pipeline."""
    async with AsyncClient(transport=MockTransport(_make_handler(500, "Error"))) as client:
        with pytest.raises(HTTPStatusError):
            await fetch_content_from_url(client, url=TEST_URL)


@pytest.mark.anyio
async def test_fetch_content_from_url_request_error() -> None:
    """Test that network connection errors raise RequestError correctly."""
    async with AsyncClient(transport=MockTransport(_make_handler(error_class=ConnectError))) as client:
        with pytest.raises(RequestError):
            await fetch_content_from_url(client, url=TEST_URL)


def test_extract_links_basic_and_relative() -> None:
    """Test standard absolute, relative links, alphabetical sorting, and uniqueness."""
    html: str = """
    <html>
        <body>
            <a href="https://example.com/about">About</a>
            <a href="/contact">Contact</a>
            <a href="/contact">Contact Duplicate</a>
            <a href="https://example.com/services">Services</a>
        </body>
    </html>
    """
    base_url: str = "https://example.com/index.html"
    result: list[str] = extract_links(html, base_url, internal_only=False)

    expected: list[str] = [
        "https://example.com/about",
        "https://example.com/contact",
        "https://example.com/services",
    ]
    assert result == expected


def test_extract_links_internal_only() -> None:
    """Test filtering for internal links only when internal_only=True."""
    html: str = """
    <html>
        <body>
            <a href="/internal-page">Internal Relative</a>
            <a href="https://example.com/another-internal">Internal Absolute</a>
            <a href="https://external.com/page">External Link</a>
            <a href="/policy/policy.pdf""></a>
        </body>
    </html>
    """
    base_url: str = "https://example.com/index.html"
    result: list[str] = extract_links(html, base_url, internal_only=True)

    expected: list[str] = [
        "https://example.com/another-internal",
        "https://example.com/internal-page",
    ]
    assert result == expected


def test_extract_links_skips_non_navigational() -> None:
    """Test that empty strings, hash anchors, javascript, mailto, and tel schemes are ignored."""
    html: str = """
    <html>
        <body>
            <a href="">Empty</a>
            <a href="#">Hash Only</a>
            <a href="javascript:void(0);">JS Action</a>
            <a href="mailto:test@example.com">Email</a>
            <a href="tel:+123456789">Phone</a>
            <a href="/valid-page">Valid Page</a>
        </body>
    </html>
    """
    base_url: str = "https://example.com"
    result: list[str] = extract_links(html, base_url)

    expected: list[str] = ["https://example.com/valid-page"]
    assert result == expected


def test_extract_links_fragment_stripping() -> None:
    """Test that URL fragments/anchors are stripped and duplicates are consolidated."""
    html: str = """
    <html>
        <body>
            <a href="/page#section1">Section 1</a>
            <a href="/page#section2">Section 2</a>
            <a href="/page">Base Page</a>
        </body>
    </html>
    """
    base_url: str = "https://example.com"
    result: list[str] = extract_links(html, base_url)

    # All variations should collapse to the same clean URL and de-duplicate
    expected: list[str] = ["https://example.com/page"]
    assert result == expected


def test_extract_links_invalid_base_url() -> None:
    """Test that ValueError is raised when base_url lacks a valid netloc/domain."""
    html_with_invalid_url: str = '<a href="/page">Page</a>'
    with pytest.raises(ValueError, match="Invalid base_url provided"):
        extract_links(html_with_invalid_url, "not-a-valid-url")
