"""Per-user secrets (e.g. the ElevenLabs API key), protected with Windows DPAPI. Never logged."""
from __future__ import annotations

import os
from pathlib import Path

from swarlekhak.config import data_dir

ENV_OVERRIDES = {"elevenlabs_api_key": ("ELEVENLABS_API_KEY", "XI_API_KEY"),
                 "gemini_api_key": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}


def _path(name: str) -> Path:
    d = data_dir() / "secrets"
    d.mkdir(exist_ok=True)
    return d / f"{name}.dpapi"


def save_secret(name: str, value: str) -> None:
    import win32crypt
    blob = win32crypt.CryptProtectData(value.encode("utf-8"), f"SwarLekhak {name}", None, None, None, 0)
    p = _path(name)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, p)


def load_secret(name: str) -> str | None:
    for env in ENV_OVERRIDES.get(name, ()):
        if os.environ.get(env):
            return os.environ[env]
    p = _path(name)
    if not p.exists():
        return None
    import win32crypt
    try:
        return win32crypt.CryptUnprotectData(p.read_bytes(), None, None, None, 0)[1].decode("utf-8")
    except Exception:
        return None


def delete_secret(name: str) -> None:
    _path(name).unlink(missing_ok=True)


def has_secret(name: str) -> bool:
    return load_secret(name) is not None
