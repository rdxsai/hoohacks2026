from __future__ import annotations

import httpx

DEFAULT_TIMEOUT = 30.0
DEFAULT_HEADERS = {
    "User-Agent": "PolicyPulse/1.0 (HooHacks 2026; policypulse@hoohacks.io)",
}

# ---------------------------------------------------------------------------
# Shared async HTTP client — reuses TCP connections across all tool calls.
# Created lazily on first use, closed via close_http_client() on shutdown.
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return the shared async HTTP client, creating it on first call."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers=DEFAULT_HEADERS,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30,
            ),
        )
    return _client


async def close_http_client() -> None:
    """Close the shared client. Call on app shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None


class APIError(Exception):
    """Raised when an external API returns a non-success response."""

    def __init__(self, *, status_code: int, url: str, detail: str) -> None:
        self.status_code = status_code
        self.url = url
        self.detail = detail
        super().__init__(f"API error {status_code} from {url}: {detail[:200]}")


async def fetch_json(
    url: str,
    *,
    params: dict | None = None,
    method: str = "GET",
    json_body: dict | None = None,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Make an HTTP request and return parsed JSON.

    Raises APIError on non-200 responses.
    """
    client = _get_client()
    response = await client.request(
        method, url,
        params=params,
        json=json_body,
        headers=headers or {},
        timeout=timeout,
    )
    if response.status_code != 200:
        raise APIError(
            status_code=response.status_code,
            url=url,
            detail=response.text[:500],
        )
    return response.json()


async def fetch_bytes(
    url: str,
    *,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[bytes, str]:
    """Fetch raw bytes from a URL. Returns (content_bytes, content_type)."""
    client = _get_client()
    response = await client.get(
        url,
        headers=headers or {},
        timeout=timeout,
    )
    if response.status_code != 200:
        raise APIError(
            status_code=response.status_code,
            url=url,
            detail=response.text[:500],
        )
    content_type = response.headers.get("content-type", "")
    return response.content, content_type
