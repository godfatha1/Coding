"""Wire configuration into authenticated providers."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Config
from .engine import Inbox, SyncEngine
from .httpc import ApiClient, Transport
from .models import SOUNDCLOUD, SPOTIFY
from .oauth import Authenticator, OAuthEndpoints
from .providers.soundcloud import SoundCloudProvider
from .providers.soundcloud_web import SoundCloudWebProvider
from .providers.spotify import SpotifyProvider
from .state import Store
from .tokens import TokenStore
from .websession import BrowserSession

log = logging.getLogger("likesync.app")


@dataclass
class App:
    cfg: Config
    store: Store
    tokens: TokenStore
    auth: dict[str, Authenticator]
    providers: dict[str, object]
    # Only set in SoundCloud web mode; owns the Chromium process.
    web_session: BrowserSession | None = None

    def inbox(self) -> Inbox | None:
        """The intake playlist, when one is configured and readable."""
        url = self.cfg.soundcloud.playlist_url.strip()
        if not url:
            return None
        provider = self.providers[SOUNDCLOUD]
        fetch = getattr(provider, "playlist_tracks", None)
        if fetch is None:
            log.warning(
                "soundcloud.playlist_url is set but %s mode cannot read "
                "playlists; ignoring it", self.cfg.soundcloud.mode,
            )
            return None
        return Inbox(provider=SOUNDCLOUD, fetch=lambda: fetch(url))

    def engine(self) -> SyncEngine:
        return SyncEngine(
            store=self.store,
            spotify=self.providers[SPOTIFY],  # type: ignore[arg-type]
            soundcloud=self.providers[SOUNDCLOUD],  # type: ignore[arg-type]
            cfg=self.cfg.sync,
            inbox=self.inbox(),
        )

    def close(self) -> None:
        if self.web_session is not None:
            self.web_session.close()
        self.store.close()


def build_app(cfg: Config, *, transport: Transport | None = None) -> App:
    store = Store(cfg.state_path)
    token_store = TokenStore(cfg.token_path)
    http = cfg.http

    def client(base: str, name: str, **kw: object) -> ApiClient:
        return ApiClient(
            base_url=base,
            transport=transport,
            timeout=http.timeout,
            max_retries=http.max_retries,
            user_agent=http.user_agent,
            name=name,
            **kw,  # type: ignore[arg-type]
        )

    # --- Spotify ---------------------------------------------------------
    sp_auth_client = client(cfg.spotify.auth_base, "spotify-auth")
    sp_auth = Authenticator(
        name=SPOTIFY,
        client_id=cfg.spotify.client_id,
        client_secret=cfg.spotify.client_secret,
        redirect_uri=cfg.spotify.redirect_uri,
        scopes=cfg.spotify.scopes,
        endpoints=OAuthEndpoints(
            authorize=f"{cfg.spotify.auth_base.rstrip('/')}/authorize",
            token=f"{cfg.spotify.auth_base.rstrip('/')}/api/token",
        ),
        store=token_store,
        client=sp_auth_client,
        # PKCE must not send a secret; only do so for a confidential app.
        include_secret=bool(cfg.spotify.client_secret),
        auth_scheme=cfg.spotify.auth_scheme,
    )
    sp_api = client(
        cfg.spotify.api_base,
        "spotify",
        auth_header=sp_auth.header,
        on_unauthorized=sp_auth.refresh_now,
    )
    spotify = SpotifyProvider(
        sp_api, sp_auth, memo=store,
        write_mode=cfg.spotify.write_mode, market=cfg.spotify.market,
    )

    # --- SoundCloud ------------------------------------------------------
    # Web mode needs no OAuth at all, but an Authenticator is still built so
    # `status` and `login` can talk about both providers uniformly.
    sc_auth_client = client(cfg.soundcloud.auth_base, "soundcloud-auth")
    sc_auth = Authenticator(
        name=SOUNDCLOUD,
        client_id=cfg.soundcloud.client_id,
        client_secret=cfg.soundcloud.client_secret,
        redirect_uri=cfg.soundcloud.redirect_uri,
        scopes=cfg.soundcloud.scopes,
        endpoints=OAuthEndpoints(
            authorize=f"{cfg.soundcloud.auth_base.rstrip('/')}/authorize",
            token=f"{cfg.soundcloud.auth_base.rstrip('/')}/oauth/token",
        ),
        store=token_store,
        client=sc_auth_client,
        # SoundCloud treats every client as confidential, so the secret rides
        # along with PKCE.
        include_secret=True,
        auth_scheme=cfg.soundcloud.auth_scheme,
    )
    sc_api = client(
        cfg.soundcloud.api_base,
        "soundcloud",
        auth_header=sc_auth.header,
        on_unauthorized=sc_auth.refresh_now,
    )
    web_session: BrowserSession | None = None
    if cfg.soundcloud.mode == "web":
        web_session = BrowserSession(
            state_path=cfg.session_path,
            executable_path=cfg.soundcloud.browser_executable or None,
            headless=cfg.soundcloud.headless,
        )
        soundcloud: object = SoundCloudWebProvider(
            web_session.page_proxy(),
            memo=store,
            max_scrolls=cfg.soundcloud.max_scrolls,
            settle_ms=cfg.soundcloud.settle_ms,
            write_pause_s=cfg.soundcloud.write_pause_s,
        )
    else:
        soundcloud = SoundCloudProvider(
            sc_api, sc_auth, memo=store, write_mode=cfg.soundcloud.write_mode
        )

    return App(
        cfg=cfg,
        store=store,
        tokens=token_store,
        auth={SPOTIFY: sp_auth, SOUNDCLOUD: sc_auth},
        providers={SPOTIFY: spotify, SOUNDCLOUD: soundcloud},
        web_session=web_session,
    )
