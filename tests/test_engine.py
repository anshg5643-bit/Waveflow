import numpy as np
import pytest

from backend import crypto, stego
from backend.synth import synth_carrier


@pytest.fixture(scope="module")
def carrier():
    return synth_carrier(15, 44100, seed=3)


def roundtrip(audio, plain, pw, strength="balanced"):
    res = stego.embed(audio, crypto.encrypt(plain, pw), crypto.slot_seed(pw), strength)
    wav = stego.write_wav(res.audio)               # real 16-bit file
    found = stego.extract(stego.read_wav(wav), crypto.slot_seed(pw))
    return res, found


@pytest.mark.parametrize("strength", list(stego.STRENGTHS))
def test_text_roundtrip(carrier, strength):
    plain = stego.pack_payload("Meet at the pier at nine.", None, None)
    res, found = roundtrip(carrier, plain, "s3cret", strength)
    assert found and crypto.decrypt(found[0], "s3cret") == plain
    assert res.snr_db > 15


def test_wrong_password_finds_nothing(carrier):
    plain = stego.pack_payload("hidden", None, None)
    res = stego.embed(carrier, crypto.encrypt(plain, "right"), crypto.slot_seed("right"))
    audio = stego.read_wav(stego.write_wav(res.audio))
    assert stego.extract(audio, crypto.slot_seed("wrong")) is None


def test_clean_audio_has_no_payload(carrier):
    assert stego.extract(stego.read_wav(stego.write_wav(carrier)), crypto.slot_seed("x")) is None


def test_binary_file_payload(carrier):
    data = np.random.default_rng(1).bytes(1500)
    plain = stego.pack_payload("note", "doc.bin", data)
    _, found = roundtrip(carrier, plain, "pw12")
    out = stego.unpack_payload(crypto.decrypt(found[0], "pw12"))
    assert out["kind"] == "file" and out["bytes"] == data and out["filename"] == "doc.bin" and out["note"] == "note"


def test_unicode_message(carrier):
    plain = stego.pack_payload("नमस्ते 🌊 héllo", None, None)
    _, found = roundtrip(carrier, plain, "pw12")
    assert stego.unpack_payload(crypto.decrypt(found[0], "pw12"))["text"] == "नमस्ते 🌊 héllo"


def test_too_large_is_rejected(carrier):
    with pytest.raises(stego.StegoError, match="too large"):
        stego.embed(carrier, crypto.encrypt(b"x" * 60000, "pw12"), 1)


def test_stereo_and_other_sample_rate():
    a = synth_carrier(10, 22050, seed=5)
    st = stego.Audio(np.hstack([a.samples, a.samples * 0.5]), a.sr)
    plain = stego.pack_payload("stereo ok", None, None)
    res = stego.embed(st, crypto.encrypt(plain, "pw12"), crypto.slot_seed("pw12"))
    wav = stego.write_wav(res.audio)
    back = stego.read_wav(wav)
    assert back.samples.shape[1] == 2
    assert np.array_equal(back.samples[:, 1], np.round(st.samples[:, 1]))   # other channel untouched
    assert stego.extract(back, crypto.slot_seed("pw12"))


def test_quiet_audio_raises_redundancy_or_fails_cleanly():
    a = synth_carrier(15, 44100, seed=9, amp=0.01)
    plain = stego.pack_payload("quiet", None, None)
    try:
        res = stego.embed(a, crypto.encrypt(plain, "pw12"), crypto.slot_seed("pw12"), "robust")
        found = stego.extract(stego.read_wav(stego.write_wav(res.audio)), crypto.slot_seed("pw12"))
        assert found and crypto.decrypt(found[0], "pw12") == plain
    except stego.StegoError:
        pass  # a clean, explained failure is acceptable


def test_tamper_detected(carrier):
    plain = stego.pack_payload("hello", None, None)
    blob = crypto.encrypt(plain, "pw12")
    bad = bytearray(blob); bad[-1] ^= 1
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(bytes(bad), "pw12")


def test_garbage_wav_rejected():
    with pytest.raises(stego.StegoError):
        stego.read_wav(b"not a wav")
