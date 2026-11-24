"""AudioSessionManager: one shared-mode capture stream, fanned out to authorized consumers.

The PortAudio callback only copies the block into a bounded queue. A worker
thread counts samples, measures level, feeds the evidence recorder (native
rate) and model consumers (16 kHz). Wispr Flow opens the same OS device
independently in shared mode; we never route or intercept its audio.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

import numpy as np

from swarlekhak.audio.resample import StreamingResampler

log = logging.getLogger(__name__)


class AudioSessionManager:
    def __init__(self, device: str | int | None = None, block_ms: int = 50) -> None:
        self.device = device if device not in ("", None) else None
        self.block_ms = block_ms
        self.native_rate = 0
        self.samples = 0                 # native-rate samples delivered to the worker
        self.dropped_blocks = 0
        self.status_flags = 0
        self.level = 0.0                 # 0..1 RMS of the latest block (drives the overlay)
        self.device_name = ""
        self._q: queue.Queue = queue.Queue(maxsize=200)
        self._stream = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._consumers: list[Callable[[np.ndarray], None]] = []
        self._recorder = None
        self._lock = threading.Lock()
        self.on_error: Callable[[str], None] | None = None
        self.started_mono = 0.0
        self.last_block_mono = 0.0

    # --- consumers
    def add_consumer(self, fn: Callable[[np.ndarray], None]) -> None:
        self._consumers.append(fn)

    def attach_recorder(self, rec) -> int:
        """Start feeding native frames to rec.write(); returns the sample index of the first frame."""
        with self._lock:
            self._recorder = rec
            return self.samples

    def detach_recorder(self) -> int:
        with self._lock:
            self._recorder = None
            return self.samples

    def stalled(self, secs: float = 3.0) -> bool:
        if self._stream is None:
            return False
        ref = self.last_block_mono or self.started_mono
        return time.monotonic() - ref > secs

    @property
    def running(self) -> bool:
        return self._stream is not None

    # --- lifecycle
    def start(self) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd
        dev = self.device
        if isinstance(dev, str) and dev.isdigit():
            dev = int(dev)
        info = sd.query_devices(dev, "input")
        self.device_name = info["name"]
        self.native_rate = int(info["default_samplerate"])
        self._resampler = StreamingResampler(self.native_rate, 16000)
        blocksize = int(self.native_rate * self.block_ms / 1000)

        def callback(indata, frames, t, status):
            if status:
                self.status_flags += 1
            try:
                self._q.put_nowait(indata[:, 0].copy())
            except queue.Full:
                self.dropped_blocks += 1

        self._stop.clear()
        self._stream = sd.InputStream(device=dev, channels=1, samplerate=self.native_rate, dtype="int16",
                                      blocksize=blocksize, callback=callback)
        self._stream.start()
        self.started_mono = time.monotonic()
        self._worker = threading.Thread(target=self._run, name="audio-worker", daemon=True)
        self._worker.start()
        log.info("capture started on %s at %d Hz", self.device_name, self.native_rate)

    def stop(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
            self._stop.set()
            if self._worker:
                self._worker.join(timeout=2)
            self.level = 0.0
            with self._q.mutex:
                self._q.queue.clear()
        log.info("capture stopped (samples=%d dropped_blocks=%d)", self.samples, self.dropped_blocks)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                block = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            self.last_block_mono = time.monotonic()
            try:
                with self._lock:
                    rec = self._recorder
                    self.samples += len(block)
                if rec is not None:
                    rec.write(block)
                f = block.astype(np.float32)
                self.level = float(min(1.0, np.sqrt(np.mean(f * f)) / 6000.0)) if len(f) else 0.0
                b16 = self._resampler.process(block)
                for c in self._consumers:
                    c(b16)
            except Exception as exc:  # never kill the worker silently
                log.exception("audio worker error")
                if self.on_error:
                    self.on_error(str(exc))
