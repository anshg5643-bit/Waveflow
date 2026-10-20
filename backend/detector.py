"""ML layer - "Stealth Analyzer": a blind steganalysis classifier.

It answers: *would a defender's phase-statistics scan flag this audio as carrying
a WaveFlow-style payload?*  Block-to-block phase-coherence features feed a
gradient-boosted classifier trained on synthetic carriers (clean vs. embedded at
random payload size/strength).

It is a red-team tool for the WaveFlow design, not a forensic product: it is
trained on synthetic music, so treat scores on real recordings as indicative.
"""
from __future__ import annotations

import math
import threading
from pathlib import Path

import joblib
import numpy as np

from . import crypto, stego
from .stego import Audio, N
from .synth import synth_carrier

MODEL_PATH = Path(__file__).parent / "models" / "detector.joblib"
FEATURE_NAMES = [
    "phase_jitter_mean", "phase_jitter_over_0.6", "phase_jitter_over_1.2",
    "phase_jitter_p25", "phase_jitter_median",
    "tonal_bin_share", "tonal_coherent_share", "tonal_jitter_mean",
    "loud_bin_fraction", "spectral_flatness",
    "inband_vs_reference_jitter", "inband_vs_reference_over_1.2",
]
_lock = threading.Lock()
_model = None


def features(audio: Audio) -> np.ndarray:
    """Temporal phase-coherence statistics.

    A steady tone advances its phase predictably from one FFT block to the next, so the
    second difference of phase is ~0. Embedding bits breaks that regularity in the bins it
    touches. Because WaveFlow dithers its lattice with a secret key there is no fixed
    pattern to look for, so a detector can only see the *accumulated* damage to coherence."""
    x = audio.samples[:, 0]
    kmin, nb, n_blocks, _ = stego._geometry(audio.n, audio.sr)  # noqa: SLF001
    spec = np.fft.rfft(x[: n_blocks * N].reshape(n_blocks, N), axis=1)[:, kmin:kmin + nb]
    mag, ph = np.abs(spec), np.angle(spec)
    d2 = np.angle(np.exp(1j * (ph[2:] - 2 * ph[1:-1] + ph[:-2])))
    mn = np.minimum(np.minimum(mag[2:], mag[1:-1]), mag[:-2])
    mx = np.maximum(np.maximum(mag[2:], mag[1:-1]), mag[:-2])

    loud = mn > 300.0
    v = np.abs(d2[loud])
    if v.size < 50:
        base = [0.0] * 5
    else:
        base = [v.mean(), np.mean(v > 0.6), np.mean(v > 1.2), np.percentile(v, 25), np.percentile(v, 50)]

    tonal = (mn > 1500.0) & ((mx - mn) < 0.15 * mx)
    t = np.abs(d2[tonal])
    if t.size < 20:
        ton = [0.0, 0.0, 0.0]
    else:
        ton = [tonal.mean(), float(np.mean(t < 0.3)), float(t.mean())]

    gm = np.exp(np.mean(np.log(mag + 1e-6)))
    flat = float(gm / (np.mean(mag) + 1e-6))

    # Same file, frequencies WaveFlow never touches (6.5-10 kHz) = built-in reference.
    r0, r1 = int(6500 * N / audio.sr), min(N // 2 - 1, int(10000 * N / audio.sr))
    ref_ratio = ref_ratio12 = 1.0
    if r1 - r0 > 16:
        rs = np.fft.rfft(x[: n_blocks * N].reshape(n_blocks, N), axis=1)[:, r0:r1]
        rm, rp = np.abs(rs), np.angle(rs)
        rd = np.abs(np.angle(np.exp(1j * (rp[2:] - 2 * rp[1:-1] + rp[:-2]))))
        rl = np.minimum(np.minimum(rm[2:], rm[1:-1]), rm[:-2]) > 100.0
        if rl.sum() > 50 and v.size >= 50:
            ref_ratio = float(v.mean() / (rd[rl].mean() + 1e-6))
            ref_ratio12 = float((np.mean(v > 1.2) + 1e-3) / (np.mean(rd[rl] > 1.2) + 1e-3))
    return np.array([*base, *ton, float(np.mean(mag > 60.0)), flat, ref_ratio, ref_ratio12], dtype=np.float64)


def _build_dataset(n_per_class: int, seed: int = 7):
    rng = np.random.default_rng(seed)
    X, y = [], []
    for i in range(n_per_class):
        sr = int(rng.choice([22050, 44100]))
        seconds = float(rng.choice([4.0, 8.0, 12.0]))
        a = synth_carrier(seconds, sr, seed=int(rng.integers(1 << 30)))
        X.append(features(a)); y.append(0)
        strength = rng.choice(list(stego.STRENGTHS))
        r = 3
        cap = stego.capacity_bytes(a.n, a.sr, r)
        fill = math.exp(rng.uniform(math.log(0.004), 0.0))  # log-uniform: tiny to full payloads
        size = max(8, int((cap - 10) * fill))
        blob = rng.bytes(size)
        pw = f"train-{i}"
        try:
            res = stego.embed(a, blob, crypto.slot_seed(pw), str(strength))
        except stego.StegoError:
            continue
        X.append(features(res.audio)); y.append(1)
    return np.array(X), np.array(y)


def train(n_per_class: int = 320, verbose: bool = True) -> dict:
    """Fit on all data; report AUC/accuracy from 5-fold stratified cross-validation
    (more stable than one holdout split, which was noisy enough at small n to land
    anywhere from 0.5 to 0.9 AUC run to run)."""
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.metrics import roc_auc_score

    X, y = _build_dataset(n_per_class)

    def make():
        return GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.06,
                                          subsample=0.8, random_state=0)

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
    oof = cross_val_predict(make(), X, y, cv=cv, method="predict_proba")[:, 1]
    auc = float(roc_auc_score(y, oof))
    acc = float(np.mean((oof > 0.5) == y))

    clf = make()
    clf.fit(X, y)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": clf, "auc": auc, "acc": acc, "n": int(len(y))}, MODEL_PATH)
    if verbose:
        print(f"detector trained on {len(y)} clips  5-fold CV AUC={auc:.3f}  acc={acc:.3f}")
    return {"auc": auc, "acc": acc, "n": int(len(y))}


def _load():
    global _model
    with _lock:
        if _model is None:
            if not MODEL_PATH.exists():
                train(verbose=False)
            _model = joblib.load(MODEL_PATH)
        return _model


def model_info() -> dict:
    m = _load()
    return {"auc": round(m["auc"], 3), "accuracy": round(m["acc"], 3), "training_clips": m["n"]}


def analyze(audio: Audio) -> dict:
    m = _load()
    f = features(audio)
    p = float(m["model"].predict_proba(f.reshape(1, -1))[0, 1])
    if p < 0.45:
        verdict, label = "clean", "Looks like ordinary audio"
    elif p < 0.75:
        verdict, label = "uncertain", "Inconclusive"
    else:
        verdict, label = "suspicious", "Phase coherence looks disturbed"
    return {
        "probability": round(p, 3),
        "verdict": verdict,
        "label": label,
        "features": {k: round(float(v), 4) for k, v in zip(FEATURE_NAMES, f)},
        "model": model_info(),
    }
