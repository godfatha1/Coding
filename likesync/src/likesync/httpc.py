"""A small HTTP client built on the standard library.

Deliberately dependency-free: this runs from cron every day for years, and a
pip resolution failure at 04:00 is a worse outcome than writing 150 lines of
retry logic. The ``Transport`` seam keeps the provider tests offline.
"""

from __future__ import annotations

import gzip
import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .errors import AuthError, ProviderError, QuotaExceeded, RateLimited

log = logging.getLogger("likesync.http")

RETRY_STATUSES = frozenset({500, 502, 503, 504, 520, 522, 524})
MAX_RETRY_AFTER_S = 300.0


@dataclass
class Response:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    url: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        if not self.body:
            return None
        try:
            return json.loads(self.body)
        except ValueError as exc:
            raise ProviderError(
                f"expected JSON from {self.url}, got {self.text[:200]!r}",
                status=self.status,
                body=self.text[:2000],
            ) from exc

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def raise_for_status(self) -> Response:
        if self.ok:
            return self
        raise ProviderError(
            f"{self.status} from {self.url}: {self.text[:300]}",
            status=self.status,
            body=self.text[:2000],
        )


class Transport(Protocol):
    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Response: ...


def _decompress(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    try:
        if encoding == "gzip":
            return gzip.decompress(raw)
        if encoding == "deflate":
            return zlib.decompress(raw)
    except Exception:  # noqa: BLE001 - pragma: no cover
        # A malformed Content-Encoding must not lose the response body.
        return raw
    return raw


class UrllibTransport:
    """Real network transport. Honours HTTPS_PROXY via urllib's ProxyHandler."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener()

    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Response:
        req = urllib.request.Request(url, data=body, method=method.upper())
        for key, value in headers.items():
            req.add_header(key, value)
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return Response(
                    status=resp.status,
                    headers=hdrs,
                    body=_decompress(raw, hdrs.get("content-encoding", "")),
                    url=resp.geturl(),
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read() if hasattr(exc, "read") else b""
            hdrs = {k.lower(): v for k, v in (exc.headers or {}).items()}
            return Response(
                status=exc.code,
                headers=hdrs,
                body=_decompress(raw, hdrs.get("content-encoding", "")),
                url=url,
            )
        except urllib.error.URLError as exc:
            raise ProviderError(f"network error for {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ProviderError(f"timeout for {url}") from exc


class ApiClient:
    """Retrying JSON client for one provider."""

    def __init__(
        self,
        *,
        base_url: str,
        transport: Transport | None = None,
        timeout: float = 30.0,
        max_retries: int = 5,
        user_agent: str = "likesync/1.0",
        auth_header: Callable[[], Mapping[str, str]] | None = None,
        on_unauthorized: Callable[[], bool] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        name: str = "api",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent
        self.auth_header = auth_header
        self.on_unauthorized = on_unauthorized
        self.sleeper = sleeper
        self.name = name
        self.request_count = 0

    # -- url helpers -------------------------------------------------------

    def url_for(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        if path.startswith(("http://", "https://")):
            url = path
        else:
            url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            clean = {
                k: ("true" if v is True else "false" if v is False else v)
                for k, v in params.items()
                if v is not None and v != ""
            }
            if clean:
                sep = "&" if urllib.parse.urlparse(url).query else "?"
                url = f"{url}{sep}{urllib.parse.urlencode(clean, doseq=True)}"
        return url

    # -- core --------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        form: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        authenticated: bool = True,
        expect: Iterable[int] | None = None,
    ) -> Response:
        """Send a request, retrying transient failures.

        Returns the final :class:`Response` for any status the server produced;
        callers inspect 403/404/405 themselves to drive endpoint fallbacks.
        ``expect`` raises :class:`ProviderError` for anything outside the set.
        """
        url = self.url_for(path, params)
        body: bytes | None = None
        base_headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            base_headers["Content-Type"] = "application/json"
        elif form is not None:
            body = urllib.parse.urlencode(form).encode("utf-8")
            base_headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif method.upper() in ("POST", "PUT"):
            # Some servers reject a bodyless POST/PUT without a length.
            base_headers["Content-Length"] = "0"
        if headers:
            base_headers.update(headers)

        refreshed = False
        attempt = 0
        while True:
            send_headers = dict(base_headers)
            if authenticated and self.auth_header:
                send_headers.update(self.auth_header())

            self.request_count += 1
            log.debug("%s %s %s", self.name, method.upper(), url)
            response = self.transport.send(
                method.upper(), url, send_headers, body, self.timeout
            )

            if response.status == 429:
                detail = response.text[:300]
                if "QUOTA_EXCEEDED" in detail.upper():
                    raise QuotaExceeded(
                        f"{self.name}: development-mode quota exhausted; "
                        "retry after the quota window resets",
                        status=429,
                        body=detail,
                    )
                wait = self._retry_after(response, attempt)
                if attempt >= self.max_retries:
                    raise RateLimited(
                        f"{self.name}: rate limited after {attempt} retries",
                        retry_after=wait,
                        status=429,
                        body=detail,
                    )
                log.warning(
                    "%s rate limited, sleeping %.1fs (attempt %d/%d)",
                    self.name, wait, attempt + 1, self.max_retries,
                )
                self.sleeper(wait)
                attempt += 1
                continue

            if response.status == 401 and authenticated and not refreshed:
                refreshed = True
                if self.on_unauthorized and self.on_unauthorized():
                    log.info("%s token refreshed after 401, retrying", self.name)
                    continue
                raise AuthError(
                    f"{self.name}: unauthorized and the token could not be "
                    f"refreshed. Run: likesync login {self.name}"
                )

            if response.status in RETRY_STATUSES and attempt < self.max_retries:
                wait = self._backoff(attempt)
                log.warning(
                    "%s got %d, retrying in %.1fs (attempt %d/%d)",
                    self.name, response.status, wait, attempt + 1, self.max_retries,
                )
                self.sleeper(wait)
                attempt += 1
                continue

            if expect is not None and response.status not in set(expect):
                raise ProviderError(
                    f"{self.name}: unexpected {response.status} from {url}: "
                    f"{response.text[:300]}",
                    status=response.status,
                    body=response.text[:2000],
                )
            return response

    def _backoff(self, attempt: int) -> float:
        return min(2.0 ** attempt, 60.0) * (1.0 + random.random() * 0.25)

    def _retry_after(self, response: Response, attempt: int) -> float:
        raw = response.header("retry-after")
        if raw:
            try:
                return min(max(float(raw), 1.0), MAX_RETRY_AFTER_S)
            except ValueError:
                pass
        return self._backoff(attempt)

    # -- conveniences ------------------------------------------------------

    def get_json(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw).raise_for_status().json()


class FakeTransport:
    """Scripted transport for tests.

    ``routes`` maps "METHOD /path" (query string ignored) to a Response, or to a
    callable taking the request and returning one. Requests are recorded.
    """

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[dict[str, Any]] = []

    def key(self, method: str, url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        return f"{method.upper()} {parsed.path}"

    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Response:
        record = {
            "method": method.upper(),
            "url": url,
            "path": urllib.parse.urlparse(url).path,
            "query": dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query)),
            "headers": dict(headers),
            "body": body,
        }
        self.calls.append(record)
        key = self.key(method, url)
        handler = self.routes.get(key)
        if handler is None:
            # Allow "POST /likes/tracks/*" style prefix routes for paths that
            # embed an id.
            for pattern, candidate in self.routes.items():
                if pattern.endswith("*") and key.startswith(pattern[:-1]):
                    handler = candidate
                    break
        if handler is None:
            return Response(status=404, body=b'{"error":"no route"}', url=url)
        if callable(handler):
            result = handler(record)
        else:
            result = handler
        if isinstance(result, Response):
            return Response(result.status, result.headers, result.body, url)
        return Response(status=200, body=json.dumps(result).encode(), url=url)


def json_response(payload: Any, status: int = 200, **headers: str) -> Response:
    return Response(
        status=status,
        headers={k.replace("_", "-").lower(): v for k, v in headers.items()},
        body=json.dumps(payload).encode("utf-8"),
    )
