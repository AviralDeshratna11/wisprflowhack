"""Graph sonification: approved data series -> real PCM tones. No trend inference."""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 44100


def frequencies(values: list[float], f_min: float = 220.0, f_max: float = 880.0) -> list[float]:
    if not values:
        return []
    y_min, y_max = min(values), max(values)
    if y_max == y_min:
        return [(f_min + f_max) / 2.0] * len(values)
    return [f_min + (y - y_min) / (y_max - y_min) * (f_max - f_min) for y in values]


def tone(freq: float, ms: float, volume: float = 0.25, sr: int = SAMPLE_RATE, env_ms: float = 12.0) -> np.ndarray:
    n = int(sr * ms / 1000.0)
    t = np.arange(n) / sr
    wave = np.sin(2 * np.pi * freq * t)
    e = max(1, int(sr * env_ms / 1000.0))
    env = np.ones(n)
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, min(e, n // 2 or 1)))
    env[:len(ramp)] = ramp
    env[n - len(ramp):] = ramp[::-1]
    return (wave * env * volume).astype(np.float32)


def render_series(values: list[float], tone_ms: float = 220.0, gap_ms: float = 60.0, volume: float = 0.25,
                  f_min: float = 220.0, f_max: float = 880.0, sr: int = SAMPLE_RATE) -> np.ndarray:
    gap = np.zeros(int(sr * gap_ms / 1000.0), dtype=np.float32)
    parts: list[np.ndarray] = []
    for f in frequencies(values, f_min, f_max):
        parts.append(tone(f, tone_ms, volume, sr))
        parts.append(gap)
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def describe_point(series: dict, idx: int, x_label: str, y_label: str) -> str:
    pts = series["points"]
    x, y = pts[idx]
    return f"Point {idx + 1} of {len(pts)}: {x_label} {_fmt(x)}, {y_label} {_fmt(y)}."


def _fmt(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
