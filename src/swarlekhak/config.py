"""Paths and user preferences (the "Voice Passport"). Writable data lives in the per-user
application-data directory, never in the installation folder."""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from swarlekhak import APP_NAME

VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Read-only resources: repo root in development, bundle dir when packaged."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    override = os.environ.get("SWARLEKHAK_DATA_DIR")
    base = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    for sub in ("", "audio", "exams", "submissions", "logs", "quarantine", "tmp", "fixtures"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


def models_dir() -> Path:
    override = os.environ.get("SWARLEKHAK_MODELS_DIR")
    if override:
        return Path(override)
    bundled = app_root() / "models"
    if (bundled / VOSK_MODEL_NAME).exists():
        return bundled
    return data_dir() / "models"


def vosk_model_path() -> Path | None:
    p = models_dir() / VOSK_MODEL_NAME
    return p if (p / "am" / "final.mdl").exists() else None


@dataclass
class Preferences:
    # activation
    wake_phrase: str = "hey swar"
    wake_enabled: bool = False                  # requires explicit consent first
    passive_wake_consent: bool = False
    activation_hotkey: str = "ctrl+alt+space"   # labelled keyboard activation fallback
    # Wispr Flow (user-configured in Flow: Settings > General > Shortcuts)
    flow_handsfree_shortcut: str = "ctrl+win+space"
    flow_cancel_shortcut: str = "esc"
    flow_mode: str = "live"                     # live | fixture (labelled demo)
    insertion_timeout_s: float = 25.0
    insertion_quiet_s: float = 1.5
    # audio
    input_device: str = ""                      # "" = system default
    tts_voice: str = ""                         # local Windows (SAPI) voice used as fallback
    tts_rate: int = 0                           # -10..10 (mapped to ElevenLabs speed 0.7..1.2)
    voice_provider: str = "gemini"              # gemini | elevenlabs (cloud, opt-in) | sapi (local)
    gemini_model: str = "gemini-3.8-flash-lite-tts"
    gemini_voice: str = "Sulafat"               # prebuilt voice ("Warm")
    gemini_consent: bool = False                # spoken text goes to Google only after explicit consent
    eleven_voice_id: str = "EMxdghWQV7gqV33j4J3F"   # "Abhi" from the ElevenLabs Voice Library
    eleven_model: str = "eleven_flash_v2_5"
    eleven_consent: bool = False                # cloud voice only after explicit consent
    follow_up_listening: bool = True            # keep listening briefly after a reply (conversation)
    follow_up_seconds: float = 6.0
    conversation_orb: bool = True
    earcons: bool = True
    # review
    review_sensitivity: str = "standard"        # standard | high
    # accessibility / UI
    reduced_motion: bool = False
    show_captions: bool = True
    language: str = "en"
    autostart: bool = False
    # exam
    exam_package: str = ""
    retain_audio_days: int = 30
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, text: str) -> "Preferences":
        raw = json.loads(text) if text else {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


class _NoSpeechFilter(logging.Filter):
    """Belt and braces: diagnostic logs must never contain dictated content."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not getattr(record, "contains_speech", False)


def setup_logging(level: int = logging.INFO) -> Path:
    log_path = data_dir() / "logs" / "swarlekhak.log"
    handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(_NoSpeechFilter())
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    if not is_frozen():
        sh = logging.StreamHandler()
        sh.addFilter(_NoSpeechFilter())
        sh.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        root.addHandler(sh)
    return log_path
