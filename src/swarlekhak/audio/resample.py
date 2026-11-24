"""Resampling to the 16 kHz model rate (off the audio callback thread)."""
from __future__ import annotations

import numpy as np


def _lowpass(cutoff: float, taps: int = 63) -> np.ndarray:
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff * n) * np.hamming(taps)
    return (h / h.sum()).astype(np.float32)


class StreamingResampler:
    """int16 mono in at src_rate -> int16 mono out at dst_rate, keeping state between blocks."""

    def __init__(self, src_rate: int, dst_rate: int = 16000) -> None:
        self.src, self.dst = int(src_rate), int(dst_rate)
        self.passthrough = self.src == self.dst
        self.integer = (not self.passthrough) and self.src % self.dst == 0
        self.factor = self.src // self.dst if self.integer else 0
        self.h = _lowpass(0.45 * self.dst / self.src) if not self.passthrough else None
        self.hist = np.zeros(0 if self.h is None else len(self.h) - 1, dtype=np.float32)
        self.phase = 0      # integer path: offset of next output sample in the filtered stream
        self.pos = 0.0      # fractional path: position in the input stream
        self.prev_last = 0.0

    def process(self, block: np.ndarray) -> np.ndarray:
        x = block.astype(np.float32).reshape(-1)
        if self.passthrough:
            return block.astype(np.int16)
        buf = np.concatenate([self.hist, x])
        y = np.convolve(buf, self.h, mode="valid")  # len == len(x)
        self.hist = buf[-(len(self.h) - 1):]
        if self.integer:
            out = y[self.phase::self.factor]
            self.phase = (self.phase - len(y)) % self.factor
        else:
            step = self.src / self.dst
            ext = np.concatenate([[self.prev_last], y])
            idx = np.arange(self.pos, len(y), step)
            out = np.interp(idx + 1, np.arange(len(ext)), ext)
            self.pos = (idx[-1] + step - len(y)) if len(idx) else self.pos - len(y)
            self.prev_last = y[-1] if len(y) else self.prev_last
        return np.clip(np.round(out), -32768, 32767).astype(np.int16)


def resample_array(x: np.ndarray, src_rate: int, dst_rate: int = 16000) -> np.ndarray:
    return StreamingResampler(src_rate, dst_rate).process(x)
