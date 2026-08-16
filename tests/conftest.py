import time
from collections.abc import Callable

import httpx
import pytest

type RequestHandler = Callable[[httpx.Request], httpx.Response]


# ======================================
# Core Data Fixtures
# ======================================


@pytest.fixture
def test_url() -> str:
    """Provides a standard test URL fixture."""
    return "https://example.com/"


@pytest.fixture
def test_html_content() -> str:
    """Provides a mock HTML string containing various link structures."""
    return """
    <html>
        <body>
            <a href="/about">About Us</a>
            <a href="https://example.com/contact">Contact</a>
            <a href="https://external.com/page">External Site</a>
            <a href="/page1.html">Page 1</a>
            <a href="/404-page.html">Dead</a>
        </body>
    </html>
    """


# ======================================
# Client Factory Fixtures
# ======================================


@pytest.fixture
def mock_client_factory() -> Callable[[RequestHandler], httpx.AsyncClient]:
    """Fixture factory to easily create an AsyncClient with a MockTransport."""

    def _create_client(handler: RequestHandler, base_url: str = "https://mocksite.com") -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)

    return _create_client


# ======================================
# Website & Navigation Request Handlers
# ======================================


@pytest.fixture
def website_handler(test_url: str, test_html_content: str) -> RequestHandler:
    """Provides a mock request handler simulating a multi-page website."""

    def handler(request: httpx.Request) -> httpx.Response:
        url: str = str(request.url)
        if url == test_url:
            return httpx.Response(200, text=test_html_content)
        elif url == f"{test_url}page1.html":
            return httpx.Response(200, text='<a href="/page2.html">Page 2</a> <a href="/">Home</a>')
        elif url == f"{test_url}page2.html":
            return httpx.Response(200, text="<p>End of line</p>")
        elif url == f"{test_url}404-page.html":
            return httpx.Response(404, text="Not Found")
        return httpx.Response(404)

    return handler


@pytest.fixture
def redirect_handler() -> RequestHandler:
    """Provides a mock request handler simulating an HTTP redirect."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/initial":
            return httpx.Response(301, headers={"Location": "https://example.com/final"})
        return httpx.Response(200, text="Final Destination Content")

    return handler


# ======================================
# Error & Exception Request Handlers
# ======================================


@pytest.fixture
def rate_limit_handler() -> RequestHandler:
    """Provides a mock request handler simulating a rate limit (429 Too Many Requests)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Too Many Requests")

    return handler


@pytest.fixture
def connection_error_handler() -> RequestHandler:
    """Provides a mock request handler that raises an httpx ConnectError."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Mocked Connection Error", request=request)

    return handler


@pytest.fixture
def timeout_handler() -> RequestHandler:
    """Provides a mock request handler that raises an httpx TimeoutException."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Mocked Timeout Exception")

    return handler


@pytest.fixture
def server_error_handler() -> RequestHandler:
    """Provides a mock request handler simulating an internal server error (500)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    return handler


@pytest.fixture
def request_error_handler() -> RequestHandler:
    """Provides a mock request handler that raises an httpx RequestError."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RequestError("Protocol Error", request=request)

    return handler


@pytest.fixture
def unexpected_error_handler() -> RequestHandler:
    """Provides a mock request handler that raises a generic RuntimeError."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("Unexpected failure")

    return handler


# ======================================
# Utility & Timing Request Handlers
# ======================================


@pytest.fixture
def timed_handler(test_url: str) -> tuple[RequestHandler, list[float]]:
    """Provides a mock request handler and a list tracking request timestamps for delay tests."""
    request_timestamps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_timestamps.append(time.monotonic())
        url: str = str(request.url)
        if url == test_url:
            return httpx.Response(200, text='<a href="/page1.html">Page 1</a>')
        return httpx.Response(200, text="<p>End</p>")

    return handler, request_timestamps
