"""Playback of evidence clips, earcons and sonified graphs (sounddevice output)."""
from __future__ import annotations

import logging
import threading
from typing import Callable

import numpy as np

from swarlekhak.engines.sonify import tone

log = logging.getLogger(__name__)


class Player:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token = 0

    def play(self, samples: np.ndarray, rate: int, on_done: Callable[[], None] | None = None) -> None:
        """Non-blocking playback; a new play() or stop() interrupts the previous one."""
        import sounddevice as sd
        with self._lock:
            self._token += 1
            token = self._token
        data = samples.astype(np.float32)
        if samples.dtype == np.int16:
            data = data / 32768.0

        def run():
            try:
                sd.play(data, rate)
                sd.wait()
            except Exception:
                log.exception("playback failed")
            if on_done and token == self._token:
                on_done()
        threading.Thread(target=run, name="player", daemon=True).start()

    def stop(self) -> None:
        import sounddevice as sd
        with self._lock:
            self._token += 1
        try:
            sd.stop()
        except Exception:
            pass

    def earcon(self, kind: str) -> None:
        if kind == "listen":
            pcm = np.concatenate([tone(660, 70, 0.18), tone(880, 90, 0.18)])
        elif kind == "done":
            pcm = np.concatenate([tone(880, 70, 0.18), tone(660, 90, 0.18)])
        else:
            pcm = tone(440, 120, 0.15)
        self.play(pcm, 44100)


def clip(pcm: np.ndarray, rate: int, start_s: float, end_s: float) -> np.ndarray:
    a = max(0, int(start_s * rate))
    b = min(len(pcm), int(end_s * rate))
    return pcm[a:b]
