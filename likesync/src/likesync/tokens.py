"""Token persistence.

Two provider quirks drive the design:

* SoundCloud refresh tokens are **single use**. Each refresh returns a
  replacement and invalidates the old one, so a lost write or two processes
  refreshing at once permanently breaks auth. Every refresh therefore happens
  under an exclusive file lock, and the file is re-read after the lock is
  taken in case another process already did the work.
* Spotify refresh tokens expire roughly six months after the original consent
  (rolled out July 2026) and PKCE refreshes may or may not return a new
  refresh token, so we persist one whenever it appears and keep the old one
  when it does not.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .errors import AuthError, ConfigError

ENV_SECRET_KEY = "LIKESYNC_SECRET_KEY"
ENV_SEED = "LIKESYNC_TOKENS"
# Refresh this far before nominal expiry to absorb clock skew and slow runs.
EXPIRY_MARGIN_S = 120.0


@dataclass
class TokenSet:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    scope: str = ""
    obtained_at: float = 0.0
    # When the user originally granted consent. Spotify anchors the refresh
    # token's six-month lifetime here, so it survives ordinary refreshes.
    consent_at: float = 0.0
    token_type: str = "Bearer"
    extra: dict = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        if not self.expires_at:
            return not self.access_token
        return time.time() >= self.expires_at - EXPIRY_MARGIN_S

    @property
    def usable(self) -> bool:
        return bool(self.access_token or self.refresh_token)

    def consent_age_days(self) -> float | None:
        if not self.consent_at:
            return None
        return (time.time() - self.consent_at) / 86400.0

    @classmethod
    def from_response(
        cls, data: dict, *, previous: TokenSet | None = None
    ) -> TokenSet:
        now = time.time()
        prev = previous or cls()
        expires_in = data.get("expires_in")
        try:
            expires_at = now + float(expires_in) if expires_in else 0.0
        except (TypeError, ValueError):
            expires_at = 0.0
        return cls(
            access_token=str(data.get("access_token") or ""),
            # Keep the existing refresh token when the response omits one.
            refresh_token=str(data.get("refresh_token") or prev.refresh_token or ""),
            expires_at=expires_at,
            scope=str(data.get("scope") or prev.scope or ""),
            obtained_at=now,
            consent_at=prev.consent_at or now,
            token_type=str(data.get("token_type") or "Bearer"),
        )


def _secret_key() -> bytes | None:
    raw = os.environ.get(ENV_SECRET_KEY, "").strip()
    if not raw:
        return None
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except Exception as exc:
        raise ConfigError(f"{ENV_SECRET_KEY} is not valid base64: {exc}") from exc
    if len(key) not in (16, 24, 32):
        raise ConfigError(
            f"{ENV_SECRET_KEY} must decode to 16, 24 or 32 bytes (got {len(key)}). "
            "Generate one with: python3 -c "
            "'import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())'"
        )
    return key


def _aesgcm(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise ConfigError(
            f"{ENV_SECRET_KEY} is set but the 'cryptography' package is missing. "
            "Install it with: pip install 'likesync[crypt]'"
        ) from exc
    return AESGCM(key)


class TokenStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold an exclusive lock across a read-refresh-write cycle."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            finally:
                os.close(fh)

    # -- serialisation -----------------------------------------------------

    def _decode(self, blob: bytes) -> dict:
        try:
            payload = json.loads(blob.decode("utf-8"))
        except Exception as exc:
            raise AuthError(f"token file {self.path} is corrupt: {exc}") from exc
        if isinstance(payload, dict) and payload.get("enc") == "aes-gcm":
            key = _secret_key()
            if key is None:
                raise ConfigError(
                    f"{self.path} is encrypted but {ENV_SECRET_KEY} is not set"
                )
            aes = _aesgcm(key)
            nonce = base64.b64decode(payload["nonce"])
            data = base64.b64decode(payload["data"])
            try:
                clear = aes.decrypt(nonce, data, b"likesync-tokens-v1")
            except Exception as exc:
                raise AuthError(
                    f"could not decrypt {self.path}; is {ENV_SECRET_KEY} the right key?"
                ) from exc
            payload = json.loads(clear.decode("utf-8"))
        return payload.get("providers", {}) if isinstance(payload, dict) else {}

    def _encode(self, providers: dict) -> bytes:
        clear = json.dumps(
            {"version": 1, "providers": providers}, indent=2, sort_keys=True
        ).encode("utf-8")
        key = _secret_key()
        if key is None:
            return clear
        aes = _aesgcm(key)
        nonce = os.urandom(12)
        sealed = aes.encrypt(nonce, clear, b"likesync-tokens-v1")
        return json.dumps(
            {
                "enc": "aes-gcm",
                "nonce": base64.b64encode(nonce).decode(),
                "data": base64.b64encode(sealed).decode(),
            },
            indent=2,
        ).encode("utf-8")

    # -- read / write ------------------------------------------------------

    def load_all(self) -> dict[str, TokenSet]:
        blob: bytes | None = None
        if self.path.is_file():
            blob = self.path.read_bytes()
        elif os.environ.get(ENV_SEED):
            # CI convenience: seed the store from a base64 JSON blob.
            raw = os.environ[ENV_SEED].strip()
            try:
                blob = base64.b64decode(raw + "=" * (-len(raw) % 4))
            except Exception as exc:
                raise ConfigError(f"{ENV_SEED} is not valid base64: {exc}") from exc
        if not blob:
            return {}
        out: dict[str, TokenSet] = {}
        for provider, data in self._decode(blob).items():
            known = {f for f in TokenSet.__dataclass_fields__}
            out[provider] = TokenSet(
                **{k: v for k, v in data.items() if k in known}
            )
        return out

    def load(self, provider: str) -> TokenSet:
        return self.load_all().get(provider, TokenSet())

    def save(self, provider: str, tokens: TokenSet) -> None:
        current = self.load_all()
        current[provider] = tokens
        self.save_all(current)

    def save_all(self, providers: dict[str, TokenSet]) -> None:
        payload = {k: asdict(v) for k, v in providers.items()}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        blob = self._encode(payload)
        # Write-and-rename so an interrupted run cannot leave a half-written
        # token file, which for SoundCloud would mean a lost refresh token.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def forget(self, provider: str) -> bool:
        current = self.load_all()
        if provider not in current:
            return False
        del current[provider]
        self.save_all(current)
        return True

    def export_b64(self) -> str:
        """Serialise the whole store for a CI secret."""
        payload = {k: asdict(v) for k, v in self.load_all().items()}
        clear = json.dumps({"version": 1, "providers": payload}, sort_keys=True)
        return base64.b64encode(clear.encode("utf-8")).decode()
