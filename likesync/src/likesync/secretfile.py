"""Read and write a JSON file that may hold credentials.

Shares the token store's encryption: when LIKESYNC_SECRET_KEY is set the file
is sealed with AES-GCM, otherwise it is plain JSON at mode 0600.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from .errors import AuthError, ConfigError
from .tokens import ENV_SECRET_KEY, _aesgcm, _secret_key

_AAD = b"likesync-secretfile-v1"


def write_secret_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    clear = json.dumps(payload, sort_keys=True).encode("utf-8")

    key = _secret_key()
    if key is None:
        blob = clear
    else:
        nonce = os.urandom(12)
        blob = json.dumps(
            {
                "enc": "aes-gcm",
                "nonce": base64.b64encode(nonce).decode(),
                "data": base64.b64encode(_aesgcm(key).encrypt(nonce, clear, _AAD)).decode(),
            }
        ).encode("utf-8")

    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def read_secret_json(path: Path) -> Any | None:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except Exception as exc:
        raise AuthError(f"{path} is corrupt: {exc}") from exc

    if isinstance(payload, dict) and payload.get("enc") == "aes-gcm":
        key = _secret_key()
        if key is None:
            raise ConfigError(f"{path} is encrypted but {ENV_SECRET_KEY} is not set")
        try:
            clear = _aesgcm(key).decrypt(
                base64.b64decode(payload["nonce"]),
                base64.b64decode(payload["data"]),
                _AAD,
            )
        except Exception as exc:
            raise AuthError(
                f"could not decrypt {path}; is {ENV_SECRET_KEY} the right key?"
            ) from exc
        return json.loads(clear.decode("utf-8"))
    return payload
