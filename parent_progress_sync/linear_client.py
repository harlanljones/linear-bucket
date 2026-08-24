"""Minimal Linear GraphQL client with pagination and rate-limit handling."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping, Sequence

USER_AGENT = "parent-progress-sync/1.0"
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRYABLE_ERROR_CODES = frozenset({"RATELIMITED", "INTERNAL_SERVER_ERROR"})

#: Error text is bounded because it can echo the request back into logs.
MAX_ERROR_CHARS = 300


class LinearError(RuntimeError):
    """Base class for Linear API failures."""


class LinearAPIError(LinearError):
    """A GraphQL or HTTP error that should not be retried."""


class RateLimitError(LinearError):
    """The request was rate limited or transiently failed."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: str


Transport = Callable[[str, Mapping[str, str], bytes], Response]


def http_transport(url: str, headers: Mapping[str, str], body: bytes) -> Response:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request) as raw:
            return Response(raw.status, dict(raw.headers), raw.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # non-2xx responses still carry a body
        return Response(exc.code, dict(exc.headers or {}), exc.read().decode("utf-8"))


class LinearClient:
    """Executes GraphQL documents against the Linear API.

    Retries rate-limited and transient responses with exponential backoff,
    honouring ``Retry-After`` when the API supplies it.
    """

    def __init__(
        self,
        api_key: str,
        api_url: str,
        *,
        max_retries: int = 5,
        transport: Transport = http_transport,
        sleep: Callable[[float], None] = time.sleep,
        base_backoff: float = 1.0,
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url
        self._max_retries = max_retries
        self._transport = transport
        self._sleep = sleep
        self._base_backoff = base_backoff

    def execute(self, query: str, variables: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": dict(variables or {})}).encode("utf-8")
        headers = {
            "Authorization": self._api_key,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

        for attempt in range(self._max_retries + 1):
            try:
                return self._request_once(headers, payload)
            except RateLimitError as exc:
                if attempt == self._max_retries:
                    raise
                self._sleep(self._backoff(attempt, exc.retry_after))

        raise AssertionError("unreachable")

    def _request_once(self, headers: Mapping[str, str], payload: bytes) -> dict[str, Any]:
        response = self._transport(self._api_url, headers, payload)

        if response.status in RETRYABLE_STATUSES:
            raise RateLimitError(
                f"Linear API returned HTTP {response.status}",
                _retry_after(response.headers),
            )
        if response.status >= 400:
            raise LinearAPIError(
                f"Linear API returned HTTP {response.status}: {_truncate(response.body)}"
            )

        try:
            document = json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise LinearAPIError(f"Linear API returned invalid JSON: {exc}") from exc

        errors = document.get("errors")
        if errors:
            message = _truncate("; ".join(str(error.get("message", error)) for error in errors))
            if any(_error_code(error) in RETRYABLE_ERROR_CODES for error in errors):
                raise RateLimitError(message, _retry_after(response.headers))
            raise LinearAPIError(message)

        data = document.get("data")
        if data is None:
            raise LinearAPIError("Linear API response contained no data")
        return data

    def paginate(
        self,
        query: str,
        variables: Mapping[str, Any],
        path: Sequence[str],
        *,
        page_size: int = 50,
    ) -> Iterator[dict[str, Any]]:
        """Yield every node of a connection, following ``pageInfo`` cursors.

        ``path`` names the keys leading to the connection in the response, e.g.
        ``("issues",)`` or ``("issue", "children")``.
        """
        cursor: str | None = None
        while True:
            data = self.execute(query, {**variables, "first": page_size, "after": cursor})
            connection = _dig(data, path)
            yield from connection.get("nodes", [])

            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return
            cursor = page_info.get("endCursor")
            if not cursor:
                return

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after
        return self._base_backoff * (2**attempt)


def _truncate(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    """Bound error text, which can echo request variables into logs."""
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "... (truncated)"


def _dig(data: Mapping[str, Any], path: Sequence[str]) -> Mapping[str, Any]:
    node: Any = data
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            raise LinearAPIError(f"Response is missing {'.'.join(path)}")
        node = node[key]
    if not isinstance(node, Mapping):
        raise LinearAPIError(f"Response field {'.'.join(path)} is not a connection")
    return node


def _error_code(error: Any) -> str:
    if not isinstance(error, Mapping):
        return ""
    extensions = error.get("extensions") or {}
    code = extensions.get("code") or extensions.get("type") or ""
    return str(code).upper()


def _retry_after(headers: Mapping[str, str]) -> float | None:
    for name, value in headers.items():
        if name.lower() != "retry-after":
            continue
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return None
    return None
