"""Generates music-like carrier audio: used for the 'Try a sample' button and
for building the steganalysis training set (so no copyrighted audio is bundled)."""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, lfilter

from .stego import Audio


def synth_carrier(seconds: float = 20.0, sr: int = 44100, seed: int = 0, amp: float | None = None) -> Audio:
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    x = np.zeros(n)

    # slow chord progression with detuned harmonic voices
    roots = rng.choice([110.0, 123.5, 130.8, 146.8, 164.8, 196.0], size=4, replace=False)
    seg = max(1, n // len(roots))
    for i, f0 in enumerate(roots):
        a, b = i * seg, n if i == len(roots) - 1 else (i + 1) * seg
        tt = t[a:b]
        env = np.minimum(1, np.minimum((tt - tt[0]) * 3, (tt[-1] - tt) * 3)) if len(tt) else tt
        for ratio in (1.0, 1.25, 1.5, 2.0):
            f = f0 * ratio * (1 + rng.normal(0, 0.002))
            for h in range(1, 7):
                x[a:b] += env * np.sin(2 * np.pi * f * h * tt + rng.uniform(0, 2 * np.pi)) / (h ** 1.3) * rng.uniform(0.6, 1.0)

    # plucked melody notes
    for _ in range(int(seconds * 2)):
        f = rng.choice([220, 261.6, 293.7, 329.6, 392, 440, 523.3, 587.3, 659.3]) * rng.choice([1, 1, 2])
        start = rng.integers(0, max(1, n - sr))
        L = int(rng.uniform(0.2, 0.9) * sr)
        tt = np.arange(L) / sr
        note = np.exp(-tt * rng.uniform(3, 8)) * (np.sin(2 * np.pi * f * tt + rng.uniform(0, 6.28))
                                                   + 0.4 * np.sin(2 * np.pi * 2 * f * tt + rng.uniform(0, 6.28))
                                                   + 0.2 * np.sin(2 * np.pi * 3 * f * tt + rng.uniform(0, 6.28)))
        x[start:start + L] += 0.5 * note[: max(0, min(L, n - start))] if start + L <= n else 0.5 * note[: n - start]

    # airy noise bed (breath / room tone) so bins are never "too clean"
    b, a = butter(2, [200 / (sr / 2), 5000 / (sr / 2)], btype="band")
    x += 0.35 * lfilter(b, a, rng.normal(0, 1, n))
    x += rng.normal(0, 0.01, n)

    x /= np.max(np.abs(x)) + 1e-9
    level = amp if amp is not None else rng.uniform(0.35, 0.8)
    return Audio((x * level * 32767.0)[:, None], sr)
