"""Layer 2 - steganography: FFT phase modulation (the WaveFlow "Phase Modulator").

Pipeline (matches the deck):  bytes -> bits -> FFT blocks -> quantise PHASE of
selected bins so that  round(phase/step) mod 2 == bit  -> inverse FFT -> 16-bit WAV.
Magnitude (the "volume shape") is never touched, only phase.

* Bin positions are a key-dependent pseudo-random permutation, so without the
  password an observer does not even know where to look.
* Each bit is repeated r times in different blocks (majority vote) and the
  encoder *verifies by decoding its own output*, raising r automatically until
  the message survives 16-bit rounding.
"""
from __future__ import annotations
from typing import Optional

import io
import math
import struct
from dataclasses import dataclass, field

import numpy as np
from scipy.io import wavfile

N = 2048                       # FFT block size
BAND_HZ = (300.0, 6000.0)      # frequencies used for hiding
MAGIC = b"WF"
HEADER_BYTES = len(MAGIC) + 4  # magic + u32 length
REPEATS = (3, 5, 7, 9)
STRENGTHS = {                  # lattice step (radians): bigger = sturdier, more audible
    "subtle": math.pi / 4,
    "balanced": math.pi / 3,
    "robust": math.pi / 2,
}
CHUNK_BLOCKS = 256


class StegoError(Exception):
    pass


# ----------------------------------------------------------------- audio I/O
@dataclass
class Audio:
    samples: np.ndarray      # shape (n, channels), float64 in int16 units
    sr: int

    @property
    def n(self) -> int:
        return self.samples.shape[0]

    @property
    def duration(self) -> float:
        return self.n / self.sr


def read_wav(data: bytes, max_seconds: float = 600) -> Audio:
    try:
        sr, x = wavfile.read(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise StegoError("Could not read this file. Upload an uncompressed PCM .wav file.") from exc
    if x.ndim == 1:
        x = x[:, None]
    if x.dtype == np.uint8:
        x = (x.astype(np.float64) - 128.0) * 256.0
    elif x.dtype == np.int16:
        x = x.astype(np.float64)
    elif x.dtype == np.int32:
        x = x.astype(np.float64) / 65536.0
    elif x.dtype in (np.float32, np.float64):
        x = x.astype(np.float64) * 32767.0
    else:
        raise StegoError(f"Unsupported WAV sample format: {x.dtype}")
    if x.shape[0] / sr > max_seconds:
        raise StegoError(f"Audio is longer than {int(max_seconds)} seconds.")
    if x.shape[0] < N * 8:
        raise StegoError("Audio is too short. Use at least ~0.5 seconds.")
    return Audio(x, int(sr))


def write_wav(audio: Audio) -> bytes:
    buf = io.BytesIO()
    out = np.clip(np.round(audio.samples), -32768, 32767).astype(np.int16)
    wavfile.write(buf, audio.sr, out if out.shape[1] > 1 else out[:, 0])
    return buf.getvalue()


def peaks(x: np.ndarray, points: int = 700) -> list[float]:
    """Downsampled |amplitude| envelope (0..1) for drawing waveforms."""
    n = len(x)
    edges = np.linspace(0, n, points + 1).astype(int)
    out = [float(np.max(np.abs(x[a:b]))) / 32768.0 if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])]
    return [round(v, 4) for v in out]


# ------------------------------------------------------------------ layout
def _band(sr: int) -> tuple[int, int]:
    kmin = max(2, int(BAND_HZ[0] * N / sr))
    kmax = min(N // 2 - 2, int(BAND_HZ[1] * N / sr))
    if kmax - kmin < 16:
        raise StegoError("Sample rate too low for hiding data.")
    return kmin, kmax


def _geometry(n_samples: int, sr: int):
    kmin, kmax = _band(sr)
    nb = kmax - kmin + 1
    n_blocks = n_samples // N
    return kmin, nb, n_blocks, n_blocks * nb


def capacity_bytes(n_samples: int, sr: int, repeats: int = 3) -> int:
    """Max size of the *encrypted blob* that fits at a given repetition."""
    _, _, _, total = _geometry(n_samples, sr)
    return max(0, ((total // repeats) // 8) - HEADER_BYTES)


def _slots(seed: int, total: int) -> np.ndarray:
    return np.random.default_rng(seed).permutation(total)


def _dither(seed: int, total: int) -> np.ndarray:
    """Secret per-bin phase offset. Shifts each bin's quantisation lattice by a
    key-derived random angle, so embedded phases look uniformly distributed to
    anyone without the key (no tell-tale clustering on multiples of the step)."""
    return np.random.default_rng([seed, 7]).uniform(-math.pi, math.pi, total)


def _wrap(a):
    return np.angle(np.exp(1j * a))


def _bits_of(blob: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(blob, dtype=np.uint8)).astype(np.int8)


def _frame(blob: bytes) -> bytes:
    return MAGIC + struct.pack(">I", len(blob)) + blob


# --------------------------------------------------------------------- embed
@dataclass
class EmbedResult:
    audio: Audio
    repeats: int
    strength: str
    step: float
    bits: int
    slots_modified: int
    max_phase_shift_deg: float
    snr_db: float
    fill_pct: float
    capacity_bytes: int
    notes: list[str] = field(default_factory=list)


def _embed_once(audio: Audio, framed: bytes, seed: int, step: float, r: int):
    sr = audio.sr
    kmin, nb, n_blocks, total = _geometry(audio.n, sr)
    bits = _bits_of(framed)
    nbits = len(bits)
    sb = total // r
    if nbits > sb:
        return None
    perm = _slots(seed, total)
    dith = _dither(seed, total)
    idx = np.arange(nbits)
    slot_ids = np.concatenate([perm[j * sb + idx] for j in range(r)])
    slot_bits = np.tile(bits, r)
    blocks_of = slot_ids // nb
    bins_of = kmin + (slot_ids % nb)
    dith_of = dith[slot_ids]
    order = np.argsort(blocks_of, kind="stable")
    blocks_of, bins_of, slot_bits, dith_of = blocks_of[order], bins_of[order], slot_bits[order], dith_of[order]

    x = audio.samples[:, 0]
    out = x.copy()
    max_shift = 0.0
    lo = 0
    for start in range(0, n_blocks, CHUNK_BLOCKS):
        stop = min(start + CHUNK_BLOCKS, n_blocks)
        hi = np.searchsorted(blocks_of, stop, side="left")
        if hi > lo:
            seg = x[start * N: stop * N].reshape(stop - start, N)
            spec = np.fft.rfft(seg, axis=1)
            b, k, want, d = blocks_of[lo:hi] - start, bins_of[lo:hi], slot_bits[lo:hi], dith_of[lo:hi]
            val = spec[b, k]
            ph = np.angle(val)
            rel = _wrap(ph - d)                       # phase relative to this bin's secret lattice
            q = np.round(rel / step).astype(np.int64)
            wrong = (q & 1) != want
            # move to the nearest lattice point of the correct parity
            direction = np.where(rel / step - q >= 0, 1, -1)
            q = np.where(wrong, q + direction, q)
            new_ph = q * step + d
            max_shift = max(max_shift, float(np.max(np.abs(_wrap(new_ph - ph)))))
            spec[b, k] = np.abs(val) * np.exp(1j * new_ph)
            out[start * N: stop * N] = np.fft.irfft(spec, n=N, axis=1).reshape(-1)
        lo = hi
    new = audio.samples.copy()
    new[:, 0] = np.clip(np.round(out), -32768, 32767)
    return new, nbits, len(slot_ids), math.degrees(max_shift)


def embed(audio: Audio, blob: bytes, seed: int, strength: str = "balanced") -> EmbedResult:
    """Hide `blob` (already encrypted) in `audio`. Verifies by decoding."""
    if strength not in STRENGTHS:
        raise StegoError("Unknown strength")
    step = STRENGTHS[strength]
    framed = _frame(blob)
    notes: list[str] = []
    for r in REPEATS:
        res = _embed_once(audio, framed, seed, step, r)
        if res is None:
            continue
        new, nbits, nslots, shift = res
        stego = Audio(new, audio.sr)
        # Round-trip through real 16-bit PCM exactly like the saved file.
        check = read_wav(write_wav(stego), max_seconds=1e9)
        got = extract(check, seed, only=(strength, r))
        if got is not None and got[0] == blob:
            err = stego.samples[:, 0] - audio.samples[:, 0]
            sig = float(np.sum(audio.samples[:, 0] ** 2)) + 1e-9
            noise = float(np.sum(err ** 2)) + 1e-9
            if r > REPEATS[0]:
                notes.append(f"Redundancy raised to x{r} so the message survives 16-bit rounding in quiet passages.")
            return EmbedResult(
                audio=stego, repeats=r, strength=strength, step=step, bits=nbits,
                slots_modified=nslots, max_phase_shift_deg=round(shift, 1),
                snr_db=round(10 * math.log10(sig / noise), 1),
                fill_pct=round(100 * nbits / (_geometry(audio.n, audio.sr)[3] // r), 2),
                capacity_bytes=capacity_bytes(audio.n, audio.sr, r), notes=notes,
            )
    cap = capacity_bytes(audio.n, audio.sr, REPEATS[0])
    if len(framed) > cap + HEADER_BYTES:
        raise StegoError(
            f"Message too large for this audio. Capacity is about {cap} bytes after encryption. "
            "Use a longer clip or a smaller payload."
        )
    raise StegoError(
        "Could not embed reliably - the clip may be too quiet or too short. "
        "Try a louder or longer carrier, or the 'robust' strength."
    )


# ------------------------------------------------------------------- extract
def _phases(audio: Audio):
    sr = audio.sr
    kmin, nb, n_blocks, total = _geometry(audio.n, sr)
    x = audio.samples[:, 0]
    out = np.empty((n_blocks, nb), dtype=np.float32)
    for start in range(0, n_blocks, CHUNK_BLOCKS):
        stop = min(start + CHUNK_BLOCKS, n_blocks)
        spec = np.fft.rfft(x[start * N: stop * N].reshape(stop - start, N), axis=1)
        out[start:stop] = np.angle(spec[:, kmin:kmin + nb])
    return out.reshape(-1), total


def extract(audio: Audio, seed: int, only: tuple[str, int] | None = None):
    """Return (blob, strength, repeats) or None. Tries every strength/redundancy."""
    ph, total = _phases(audio)
    perm = _slots(seed, total)
    dith = _dither(seed, total)
    strengths = [only[0]] if only else list(STRENGTHS)
    repeats = [only[1]] if only else list(REPEATS)
    for sname in strengths:
        step = STRENGTHS[sname]
        parity = (np.round(_wrap(ph - dith) / step).astype(np.int64) & 1).astype(np.int8)  # per slot position
        for r in repeats:
            sb = total // r
            if sb < HEADER_BYTES * 8:
                continue

            def read(nbits: int) -> np.ndarray:
                idx = np.arange(nbits)
                votes = sum(parity[perm[j * sb + idx]].astype(np.int32) for j in range(r))
                return (votes * 2 > r).astype(np.uint8)

            head = np.packbits(read(HEADER_BYTES * 8)).tobytes()
            if head[:2] != MAGIC:
                continue
            length = struct.unpack(">I", head[2:6])[0]
            if length <= 0 or (HEADER_BYTES + length) * 8 > sb:
                continue
            body = np.packbits(read((HEADER_BYTES + length) * 8)).tobytes()[HEADER_BYTES:]
            return body, sname, r
    return None


# ----------------------------------------------------------- payload wrapper
def pack_payload(text: str | None, file_name: str | None, file_bytes: bytes | None) -> bytes:
    if file_bytes is not None:
        name = (file_name or "payload.bin").encode("utf-8")[:255]
        head = b"F" + bytes([len(name)]) + name
        note = (text or "").encode("utf-8")
        return head + struct.pack(">I", len(note)) + note + file_bytes
    return b"T" + (text or "").encode("utf-8")


def unpack_payload(data: bytes) -> dict:
    if data[:1] == b"T":
        return {"kind": "text", "text": data[1:].decode("utf-8", errors="replace")}
    if data[:1] == b"F":
        n = data[1]
        name = data[2:2 + n].decode("utf-8", errors="replace")
        off = 2 + n
        (tl,) = struct.unpack(">I", data[off:off + 4])
        note = data[off + 4: off + 4 + tl].decode("utf-8", errors="replace")
        return {"kind": "file", "filename": name, "note": note, "bytes": data[off + 4 + tl:]}
    raise StegoError("Unknown payload format")
