import json
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.update(DATA_DIR=_tmp, GOOGLE_CLIENT_ID="test-client.apps.googleusercontent.com",
                  GROQ_API_KEY="test-key", JWT_SECRET="x" * 40, ALLOW_GUEST_LOGIN="true")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import auth, chat, main  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


def guest(c):
    r = c.post("/api/auth/guest")
    assert r.status_code == 200
    return r.json()["user"]


def sample(c):
    return c.get("/api/sample?seconds=12").content


def test_requires_login(client):
    assert client.get("/api/history").status_code == 401
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_config_and_static(client):
    cfg = client.get("/api/config").json()
    assert cfg["google_client_id"].startswith("test-client") and cfg["chat_enabled"]
    assert "WaveFlow" in client.get("/").text


def test_google_login_flow(client, monkeypatch):
    monkeypatch.setattr(auth, "verify_google_token", lambda cred: {"sub": "123", "email": "a@b.com", "name": "Ada", "picture": "http://x/y.png"})
    r = client.post("/api/auth/google", json={"credential": "fake"})
    assert r.status_code == 200 and r.json()["user"]["email"] == "a@b.com"
    assert client.get("/api/auth/me").json()["user"]["name"] == "Ada"
    # same Google account -> same user row
    first = r.json()["user"]["id"]
    again = client.post("/api/auth/google", json={"credential": "fake"}).json()["user"]["id"]
    assert first == again
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_google_rejects_bad_token(client, monkeypatch):
    def bad(*a, **k):
        raise ValueError("Wrong number of segments in token")
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", bad)
    assert client.post("/api/auth/google", json={"credential": "garbage"}).status_code == 401


def test_google_network_failure_is_503(client, monkeypatch):
    def down(*a, **k):
        raise auth.g_exc.TransportError("offline")
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", down)
    assert client.post("/api/auth/google", json={"credential": "x"}).status_code == 503


def test_full_hide_reveal_cycle(client):
    guest(client)
    wav = sample(client)
    files = {"audio": ("song.wav", wav, "audio/wav")}
    info = client.post("/api/inspect", files=files).json()
    assert info["capacity_bytes"] > 1000

    r = client.post("/api/encode", files=files, data={"password": "open-sesame", "message": "The eagle lands at dawn", "strength": "balanced"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["metrics"]["snr_db"] > 15 and 0 <= out["stealth"]["probability"] <= 1
    stego_wav = client.get(out["audio_url"]).content
    assert stego_wav[:4] == b"RIFF"

    ok = client.post("/api/decode", files={"audio": ("s.wav", stego_wav, "audio/wav")}, data={"password": "open-sesame"}).json()
    assert ok["found"] and ok["text"] == "The eagle lands at dawn"
    bad = client.post("/api/decode", files={"audio": ("s.wav", stego_wav, "audio/wav")}, data={"password": "nope-nope"}).json()
    assert bad["found"] is False

    an = client.post("/api/analyze", files={"audio": ("s.wav", stego_wav, "audio/wav")}).json()
    assert "probability" in an and len(an["peaks"]) == 700

    hist = client.get("/api/history").json()["operations"]
    assert {o["kind"] for o in hist} == {"encode", "decode", "analyze"}
    enc = next(o for o in hist if o["kind"] == "encode")
    assert client.delete(f"/api/history/{enc['id']}").status_code == 200
    assert client.get(out["audio_url"]).status_code == 404


def test_file_payload_cycle(client):
    guest(client)
    secret = bytes(range(256)) * 8
    wav = sample(client)
    r = client.post("/api/encode", files={"audio": ("a.wav", wav, "audio/wav"), "payload_file": ("secret.bin", secret)},
                    data={"password": "pw-pw-pw", "message": "see attached"})
    assert r.status_code == 200, r.text
    stego_wav = client.get(r.json()["audio_url"]).content
    d = client.post("/api/decode", files={"audio": ("s.wav", stego_wav)}, data={"password": "pw-pw-pw"}).json()
    assert d["kind"] == "file" and d["filename"] == "secret.bin" and d["note"] == "see attached"
    assert client.get(d["download_url"]).content == secret


def test_files_are_private_between_users(client):
    guest(client)
    wav = sample(client)
    out = client.post("/api/encode", files={"audio": ("a.wav", wav)}, data={"password": "pw-pw-pw", "message": "x"}).json()
    with TestClient(main.app) as other:
        guest(other)
        assert other.get(out["audio_url"]).status_code == 404


def test_validation_errors(client):
    guest(client)
    wav = sample(client)
    assert client.post("/api/encode", files={"audio": ("a.wav", wav)}, data={"password": "abc", "message": "x"}).status_code == 400
    assert client.post("/api/encode", files={"audio": ("a.wav", wav)}, data={"password": "abcd", "message": ""}).status_code == 400
    assert client.post("/api/encode", files={"audio": ("a.mp3", b"ID3notwav")}, data={"password": "abcd", "message": "x"}).status_code == 400
    assert client.post("/api/encode", files={"audio": ("a.wav", wav)}, data={"password": "abcd", "message": "x" * 20000}).status_code == 422


class FakeGroq(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for c in self.chunks:
            yield c


def _patch_groq(monkeypatch, status=200):
    sent = {}

    def handler(request: httpx.Request):
        sent["auth"] = request.headers["authorization"]
        sent["body"] = json.loads(request.content)
        if status != 200:
            return httpx.Response(status, json={"error": "nope"})
        lines = [f'data: {json.dumps({"choices": [{"delta": {"content": w}}]})}\n\n'.encode() for w in ("Phase ", "hiding ", "rocks")]
        return httpx.Response(200, stream=FakeGroq(lines + [b"data: [DONE]\n\n"]), headers={"content-type": "text/event-stream"})

    real = httpx.AsyncClient
    monkeypatch.setattr(chat.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return sent


def test_chat_streams_and_persists(client, monkeypatch):
    guest(client)
    sent = _patch_groq(monkeypatch)
    r = client.post("/api/chat", json={"message": "How does it work?"})
    assert r.status_code == 200
    events = [json.loads(l[5:]) for l in r.text.splitlines() if l.startswith("data:")]
    assert "".join(e.get("delta", "") for e in events) == "Phase hiding rocks"
    assert sent["auth"] == "Bearer test-key" and sent["body"]["stream"] is True
    assert sent["body"]["messages"][0]["role"] == "system"
    hist = client.get("/api/chat/history").json()["messages"]
    assert [m["role"] for m in hist] == ["user", "assistant"] and hist[1]["content"] == "Phase hiding rocks"
    client.delete("/api/chat/history")
    assert client.get("/api/chat/history").json()["messages"] == []


def test_chat_surfaces_groq_errors(client, monkeypatch):
    guest(client)
    _patch_groq(monkeypatch, status=401)
    r = client.post("/api/chat", json={"message": "hi"})
    assert "rejected the API key" in r.text


def test_chat_without_key(client, monkeypatch):
    guest(client)
    monkeypatch.setattr(chat.config, "GROQ_API_KEY", "")
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 503
