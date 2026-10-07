"""Wire configuration into authenticated providers."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Config
from .engine import SyncEngine
from .httpc import ApiClient, Transport
from .models import SOUNDCLOUD, SPOTIFY
from .oauth import Authenticator, OAuthEndpoints
from .providers.soundcloud import SoundCloudProvider
from .providers.spotify import SpotifyProvider
from .state import Store
from .tokens import TokenStore

log = logging.getLogger("likesync.app")


@dataclass
class App:
    cfg: Config
    store: Store
    tokens: TokenStore
    auth: dict[str, Authenticator]
    providers: dict[str, object]

    def engine(self) -> SyncEngine:
        return SyncEngine(
            store=self.store,
            spotify=self.providers[SPOTIFY],  # type: ignore[arg-type]
            soundcloud=self.providers[SOUNDCLOUD],  # type: ignore[arg-type]
            cfg=self.cfg.sync,
        )

    def close(self) -> None:
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
    soundcloud = SoundCloudProvider(
        sc_api, sc_auth, memo=store, write_mode=cfg.soundcloud.write_mode
    )

    return App(
        cfg=cfg,
        store=store,
        tokens=token_store,
        auth={SPOTIFY: sp_auth, SOUNDCLOUD: sc_auth},
        providers={SPOTIFY: spotify, SOUNDCLOUD: soundcloud},
    )
