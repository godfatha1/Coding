"""OAuth 2 authorization-code flow with PKCE, plus token refresh."""

from __future__ import annotations

import base64
import hashlib
import http.server
import logging
import secrets
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from .errors import AuthError
from .httpc import ApiClient
from .tokens import TokenSet, TokenStore

log = logging.getLogger("likesync.oauth")


def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for the S256 method."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>likesync</title>
<style>
 body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#111;color:#eee;
      display:grid;place-items:center;height:100vh;margin:0}}
 .card{{text-align:center;padding:2rem 3rem;background:#1c1c1c;border-radius:14px;
       border:1px solid #333;max-width:28rem}}
 h1{{font-size:1.25rem;margin:0 0 .5rem}} p{{color:#aaa;margin:0;line-height:1.5}}
 .ok{{color:#1db954}} .bad{{color:#ff7043}}
</style></head>
<body><div class="card"><h1 class="{cls}">{heading}</h1><p>{body}</p></div></body></html>
"""


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server_version = "likesync"
    # Class-level on purpose: the server instantiates a handler per request,
    # so this is how the captured params get back to the caller.
    result: ClassVar[dict[str, str]] = {}
    expected_path: ClassVar[str] = "/callback"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != self.expected_path:
            self.send_error(404, "not the callback path")
            return
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        type(self).result = params

        if "error" in params:
            page = _PAGE.format(
                cls="bad",
                heading="Authorization failed",
                body=f"{params.get('error')}: {params.get('error_description', '')}",
            )
        else:
            page = _PAGE.format(
                cls="ok",
                heading="Connected",
                body="You can close this tab and return to the terminal.",
            )
        encoded = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args: object) -> None:  # silence stdlib logging
        log.debug("callback server: " + str(args[0]) % args[1:] if args else "")


def wait_for_callback(redirect_uri: str, timeout: float = 300.0) -> dict[str, str]:
    """Run a one-shot loopback server and return the callback query params."""
    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    handler = type("Handler", (_CallbackHandler,), {"expected_path": parsed.path or "/"})
    handler.result = {}
    try:
        server = http.server.HTTPServer((host, port), handler)
    except OSError as exc:
        raise AuthError(
            f"cannot listen on {host}:{port} for the OAuth redirect ({exc}). "
            "Free the port, or log in with --manual."
        ) from exc

    server.timeout = 1.0
    deadline = time.time() + timeout
    thread = threading.Thread(target=_serve_until, args=(server, handler, deadline),
                              daemon=True)
    thread.start()
    thread.join(timeout + 5)
    server.server_close()

    if not handler.result:
        raise AuthError("timed out waiting for the OAuth redirect")
    return dict(handler.result)


def _serve_until(server: http.server.HTTPServer, handler: type, deadline: float) -> None:
    while not handler.result and time.time() < deadline:
        server.handle_request()


@dataclass
class OAuthEndpoints:
    authorize: str
    token: str


class Authenticator:
    """Owns one provider's tokens: login, refresh, and the auth header."""

    def __init__(
        self,
        *,
        name: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: str,
        endpoints: OAuthEndpoints,
        store: TokenStore,
        client: ApiClient,
        include_secret: bool = False,
        auth_scheme: str = "Bearer",
        extra_authorize_params: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.endpoints = endpoints
        self.store = store
        self.client = client
        # SoundCloud treats every client as confidential and wants the secret
        # even alongside PKCE; Spotify's PKCE flow must not send one.
        self.include_secret = include_secret
        self.auth_scheme = auth_scheme or "Bearer"
        self.extra_authorize_params = dict(extra_authorize_params or {})
        self._cached: TokenSet | None = None

    # -- login -------------------------------------------------------------

    def build_authorize_url(self, state: str, challenge: str) -> str:
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **self.extra_authorize_params,
        }
        if self.scopes:
            params["scope"] = self.scopes
        return f"{self.endpoints.authorize}?{urllib.parse.urlencode(params)}"

    def login(self, *, manual: bool = False, open_browser: bool = True) -> TokenSet:
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        url = self.build_authorize_url(state, challenge)

        print(f"\nAuthorize likesync with {self.name}:\n\n  {url}\n")
        if manual:
            print(
                "After approving, your browser lands on a URL that will not load.\n"
                "That is expected. Copy the whole address bar and paste it here."
            )
            pasted = input("Redirect URL: ").strip()
            params = self._params_from_pasted(pasted)
        else:
            if open_browser:
                try:
                    webbrowser.open(url)
                except Exception as exc:  # noqa: BLE001 - pragma: no cover
                    # No browser here is fine: the URL is printed above and
                    # --manual exists for exactly this case.
                    log.debug("could not open a browser: %s", exc)
            print(f"Waiting for the redirect to {self.redirect_uri} ...")
            params = wait_for_callback(self.redirect_uri)

        if "error" in params:
            raise AuthError(
                f"{self.name} refused authorization: {params['error']} "
                f"{params.get('error_description', '')}".strip()
            )
        returned_state = params.get("state")
        if returned_state and returned_state != state:
            raise AuthError(
                f"{self.name}: OAuth state mismatch; aborting in case of CSRF"
            )
        code = params.get("code")
        if not code:
            raise AuthError(f"{self.name}: redirect carried no authorization code")

        tokens = self.exchange(code, verifier)
        with self.store.locked():
            self.store.save(self.name, tokens)
        self._cached = tokens
        return tokens

    @staticmethod
    def _params_from_pasted(pasted: str) -> dict[str, str]:
        if not pasted:
            raise AuthError("no URL pasted")
        parsed = urllib.parse.urlparse(pasted)
        query = parsed.query or parsed.fragment
        if not query and "=" in pasted:
            query = pasted
        return {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}

    # -- token endpoint ----------------------------------------------------

    def _token_request(self, form: dict[str, str], *, previous: TokenSet | None) -> TokenSet:
        if self.include_secret and self.client_secret:
            form["client_secret"] = self.client_secret
        response = self.client.request(
            "POST",
            self.endpoints.token,
            form=form,
            authenticated=False,
        )
        if not response.ok:
            detail = response.text[:400]
            if response.status in (400, 401) and "invalid_grant" in detail:
                raise AuthError(
                    f"{self.name}: the refresh token is no longer valid "
                    f"(invalid_grant). Run: likesync login {self.name}\n{detail}"
                )
            raise AuthError(
                f"{self.name}: token endpoint returned {response.status}: {detail}"
            )
        payload = response.json() or {}
        if not payload.get("access_token"):
            raise AuthError(f"{self.name}: token response had no access_token")
        return TokenSet.from_response(payload, previous=previous)

    def exchange(self, code: str, verifier: str) -> TokenSet:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "code_verifier": verifier,
            },
            previous=None,
        )

    def refresh(self, tokens: TokenSet) -> TokenSet:
        if not tokens.refresh_token:
            raise AuthError(
                f"{self.name}: no refresh token stored. Run: likesync login {self.name}"
            )
        return self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": self.client_id,
            },
            previous=tokens,
        )

    # -- use ---------------------------------------------------------------

    def current(self) -> TokenSet:
        if self._cached is None:
            self._cached = self.store.load(self.name)
        return self._cached

    def access_token(self) -> str:
        tokens = self.current()
        if not tokens.usable:
            raise AuthError(
                f"{self.name}: not connected. Run: likesync login {self.name}"
            )
        if tokens.expired:
            self.refresh_now()
            tokens = self.current()
        return tokens.access_token

    def refresh_now(self) -> bool:
        """Refresh under an exclusive lock.

        SoundCloud invalidates a refresh token the moment it is redeemed, so
        the file is re-read inside the lock: if another process refreshed while
        we waited, we adopt its result instead of burning our stale token.
        """
        with self.store.locked():
            stored = self.store.load(self.name)
            if stored.usable and not stored.expired and stored.access_token:
                if stored.access_token != (self._cached.access_token if self._cached else None):
                    log.info("%s: adopted a token refreshed by another process", self.name)
                self._cached = stored
                return True
            if not stored.refresh_token:
                return False
            log.info("%s: refreshing access token", self.name)
            fresh = self.refresh(stored)
            self.store.save(self.name, fresh)
            self._cached = fresh
            return True

    def header(self) -> dict[str, str]:
        return {"Authorization": f"{self.auth_scheme} {self.access_token()}"}
