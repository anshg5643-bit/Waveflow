"""WaveFlow Assistant - streams answers from Groq (OpenAI-compatible API).

The API key lives only on the server (GROQ_API_KEY in .env); the browser talks to
/api/chat and never sees it. History is stored per user in the database.
"""
from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import config
from .auth import get_current_user
from .database import ChatMessage, Operation, SessionLocal, User

router = APIRouter(prefix="/api/chat", tags=["chat"])

SYSTEM_PROMPT = """You are the WaveFlow Assistant, built into the WaveFlow web app by team Dig Busters.

WaveFlow hides encrypted data inside ordinary WAV audio. How it works:
1. The message or file is encrypted with AES-256-GCM (key from the user's password via scrypt).
2. The audio is split into 2048-sample blocks and transformed with an FFT.
3. Bits are embedded in the PHASE of selected frequency bins (300 Hz - 6 kHz) by quantising phase to a lattice
   whose parity encodes the bit. Magnitude is untouched. Which bins are used depends on a key-derived permutation.
4. Each bit is repeated 3-9 times with majority voting; the encoder decodes its own output to verify.
5. An inverse FFT rebuilds a playable 16-bit WAV.
Strength presets: subtle (pi/4 phase step), balanced (pi/3), robust (pi/2). Higher = sturdier but lower SNR.
App tabs: Encode (hide), Decode (reveal), Stealth Check (ML detector that estimates whether a file looks engineered), History.

Be honest about limits: this is an MVP, not a certified security product. Hiding survives lossless copying but NOT
MP3/AAC compression, resampling or trimming. SNR is typically 20-30 dB, so "zero distortion" is not literally true;
tiny payloads in long clips are the hardest to detect, large payloads are statistically detectable by phase-lattice
analysis. Never claim it makes illegal activity safe. If asked for help hiding clearly illegal content, decline.
Keep answers concise, practical and friendly. Use short paragraphs or bullets. If you do not know, say so."""


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def _history(user_id: int, limit: int = 16) -> list[dict]:
    with SessionLocal() as db:
        rows = (db.query(ChatMessage).filter(ChatMessage.user_id == user_id)
                .order_by(ChatMessage.id.desc()).limit(limit).all())
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


def _recent_ops(user_id: int) -> str:
    with SessionLocal() as db:
        ops = (db.query(Operation).filter(Operation.user_id == user_id)
               .order_by(Operation.created_at.desc()).limit(5).all())
    if not ops:
        return "The user has not run any operations yet."
    lines = []
    for o in ops:
        m = o.meta_dict()
        bits = ", ".join(f"{k}={m[k]}" for k in ("snr_db", "fill_pct", "repeats", "strength", "probability", "ok") if k in m)
        lines.append(f"- {o.kind} on '{o.input_name}' ({bits})")
    return "The user's most recent operations:\n" + "\n".join(lines)


def _save(user_id: int, role: str, content: str) -> None:
    with SessionLocal() as db:
        db.add(ChatMessage(user_id=user_id, role=role, content=content))
        db.commit()


@router.get("/history")
def history(user: User = Depends(get_current_user)):
    return {"messages": _history(user.id, 60), "enabled": bool(config.GROQ_API_KEY)}


@router.delete("/history")
def clear_history(user: User = Depends(get_current_user)):
    with SessionLocal() as db:
        db.query(ChatMessage).filter(ChatMessage.user_id == user.id).delete()
        db.commit()
    return {"ok": True}


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


@router.post("")
async def chat(body: ChatBody, user: User = Depends(get_current_user)):
    if not config.GROQ_API_KEY:
        raise HTTPException(503, "Chatbot is not configured. Add GROQ_API_KEY to your .env file and restart.")
    uid = user.id
    _save(uid, "user", body.message)
    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + _recent_ops(uid)}, *_history(uid)]
    payload = {"model": config.GROQ_MODEL, "messages": messages, "stream": True, "temperature": 0.4, "max_tokens": 900}
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}", "Content-Type": "application/json"}

    async def gen():
        reply = ""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                async with client.stream("POST", f"{config.GROQ_BASE_URL}/chat/completions",
                                         json=payload, headers=headers) as r:
                    if r.status_code != 200:
                        detail = (await r.aread()).decode("utf-8", "replace")[:300]
                        msg = {401: "Groq rejected the API key. Check GROQ_API_KEY.",
                               429: "Groq rate limit reached. Try again in a moment."}.get(
                            r.status_code, f"Groq error {r.status_code}: {detail}")
                        yield _sse({"error": msg})
                        return
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
                        except (ValueError, KeyError, IndexError):
                            continue
                        if delta:
                            reply += delta
                            yield _sse({"delta": delta})
        except httpx.HTTPError as exc:
            yield _sse({"error": f"Could not reach Groq: {exc.__class__.__name__}"})
        finally:
            if reply:
                _save(uid, "assistant", reply)
        yield _sse({"done": True})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
