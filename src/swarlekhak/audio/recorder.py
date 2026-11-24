"""Evidence recorder: authorized dictation turns only.

Frames stream into a .part WAV under the per-user data dir while the turn is
active (so a mic failure or crash still leaves a partial turn). On stop the WAV
is hashed, encrypted with the DPAPI-protected local key and the .part removed.
Leftover .part files are quarantined (encrypted) on next start.
"""
from __future__ import annotations

import io
import logging
import os
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from swarlekhak.engines.evidence_chain import sha256_bytes
from swarlekhak.security.crypto import decrypt, encrypt

log = logging.getLogger(__name__)


@dataclass
class AudioAsset:
    path: str
    sha256: str            # of the plaintext WAV bytes
    sample_rate: int
    num_samples: int
    encrypted: bool = True


def wav_bytes(pcm: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit PCM WAV is supported")
        rate, ch = w.getframerate(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if ch > 1:
        pcm = pcm.reshape(-1, ch)[:, 0].copy()
    return pcm, rate


class EvidenceVault:
    def __init__(self, audio_dir: Path, quarantine_dir: Path, key_provider) -> None:
        self.audio_dir = Path(audio_dir)
        self.quarantine_dir = Path(quarantine_dir)
        self.key_provider = key_provider

    def store_wav(self, name: str, data: bytes) -> AudioAsset:
        pcm, rate = read_wav(data)
        path = self.audio_dir / f"{name}.wav.enc"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(encrypt(self.key_provider(), data, aad=name.encode()))
        os.replace(tmp, path)
        return AudioAsset(str(path), sha256_bytes(data), rate, len(pcm))

    def load_wav(self, path: str) -> bytes:
        name = Path(path).name.removesuffix(".wav.enc")
        data = decrypt(self.key_provider(), Path(path).read_bytes(), aad=name.encode())
        return data

    def quarantine_partials(self) -> list[str]:
        moved = []
        for part in self.audio_dir.glob("*.part"):
            try:
                data = part.read_bytes()
                out = self.quarantine_dir / (part.stem + ".partial.enc")
                out.write_bytes(encrypt(self.key_provider(), data, aad=b"quarantine"))
                part.unlink()
                moved.append(str(out))
            except Exception:
                log.exception("could not quarantine %s", part.name)
        return moved


class EvidenceRecorder:
    """Receives native-rate int16 frames from AudioSessionManager while a turn is active."""

    def __init__(self, vault: EvidenceVault) -> None:
        self.vault = vault
        self._lock = threading.Lock()
        self._wav: wave.Wave_write | None = None
        self._part: Path | None = None
        self._name = ""
        self.frames = 0
        self.rate = 0

    def start(self, name: str, rate: int) -> None:
        with self._lock:
            if self._wav is not None:
                raise RuntimeError("a turn is already being recorded")
            self._name = name
            self.rate = rate
            self.frames = 0
            self._part = self.vault.audio_dir / f"{name}.part"
            self._wav = wave.open(str(self._part), "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(rate)

    def write(self, block: np.ndarray) -> None:
        with self._lock:
            if self._wav is not None:
                self._wav.writeframes(block.astype(np.int16).tobytes())
                self.frames += len(block)

    @property
    def active(self) -> bool:
        return self._wav is not None

    def stop(self) -> AudioAsset | None:
        with self._lock:
            if self._wav is None:
                return None
            self._wav.close()
            self._wav = None
            part = self._part
        data = part.read_bytes()
        asset = self.vault.store_wav(self._name, data)
        part.unlink(missing_ok=True)
        return asset
