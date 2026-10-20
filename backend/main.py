"""WaveFlow API + static frontend.   Run:  uvicorn backend.main:app --reload"""
from __future__ import annotations

import json
import re
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from typing import Optional
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import auth, chat, config, crypto, detector, stego
from .database import Operation, User, get_db, init_db
from .synth import synth_carrier

FRONTEND = config.ROOT / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    threading.Thread(target=detector._load, daemon=True).start()  # noqa: SLF001  warm the ML model
    yield


app = FastAPI(title="WaveFlow", version="1.0.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(chat.router)


# ------------------------------------------------------------------ helpers
def _read_upload(f: UploadFile, limit_mb: int | None = None) -> bytes:
    limit = (limit_mb or config.MAX_UPLOAD_MB) * 1024 * 1024
    data = f.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File is larger than {limit_mb or config.MAX_UPLOAD_MB} MB.")
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    return data


def _load_audio(f: UploadFile) -> stego.Audio:
    try:
        return stego.read_wav(_read_upload(f), max_seconds=config.MAX_AUDIO_SECONDS)
    except stego.StegoError as exc:
        raise HTTPException(400, str(exc)) from exc


def _safe_stem(name: str) -> str:
    stem = Path(name or "audio").stem
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem)[:60] or "audio"


def _record(db: Session, user: User, kind: str, input_name: str, meta: dict,
            out_path: Path | None = None, out_name: str | None = None, op_id: str | None = None) -> Operation:
    op = Operation(id=op_id or uuid.uuid4().hex, user_id=user.id, kind=kind, input_name=input_name[:255],
                   meta=json.dumps(meta), output_path=out_path.name if out_path else None, output_name=out_name)
    db.add(op)
    db.commit()
    return op


def _op_public(op: Operation) -> dict:
    return {
        "id": op.id, "kind": op.kind, "input_name": op.input_name, "meta": op.meta_dict(),
        "created_at": op.created_at.isoformat() + "Z",
        "download_url": f"/api/files/{op.id}?download=1" if op.output_path else None,
        "output_name": op.output_name,
    }


# ------------------------------------------------------------------- public
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def public_config():
    return {
        "google_client_id": config.GOOGLE_CLIENT_ID,
        "guest_enabled": config.ALLOW_GUEST_LOGIN,
        "chat_enabled": bool(config.GROQ_API_KEY),
        "chat_model": config.GROQ_MODEL,
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "strengths": list(stego.STRENGTHS),
    }


@app.get("/api/sample")
def sample(seconds: int = 20, seed: int = 11):
    """A generated music-like WAV so people can try the app without their own audio."""
    seconds = max(5, min(seconds, 60))
    audio = synth_carrier(seconds, 44100, seed=seed, amp=0.6)
    return Response(stego.write_wav(audio), media_type="audio/wav",
                    headers={"Content-Disposition": 'attachment; filename="waveflow_sample.wav"'})


# ---------------------------------------------------------------- protected
@app.post("/api/inspect")
def inspect(audio: UploadFile = File(...), user: User = Depends(auth.get_current_user)):
    a = _load_audio(audio)
    cap = stego.capacity_bytes(a.n, a.sr, 3)
    overhead = crypto.OVERHEAD + 1
    return {
        "duration": round(a.duration, 2), "sample_rate": a.sr, "channels": a.samples.shape[1],
        "capacity_bytes": max(0, cap - overhead),
        "stealth_budget_bytes": max(0, int(cap * 0.02) - overhead),
        "peaks": stego.peaks(a.samples[:, 0]),
    }


@app.post("/api/encode")
def encode(
    audio: UploadFile = File(...),
    password: str = Form(...),
    message: str = Form(""),
    strength: str = Form("balanced"),
    payload_file: Optional[UploadFile] = File(None),
    user: User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    if len(password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters.")
    if strength not in stego.STRENGTHS:
        raise HTTPException(400, "Unknown strength preset.")
    file_bytes = file_name = None
    if payload_file is not None and payload_file.filename:
        file_bytes, file_name = _read_upload(payload_file, 5), payload_file.filename
    if not message.strip() and file_bytes is None:
        raise HTTPException(400, "Enter a message or attach a file to hide.")

    carrier = _load_audio(audio)
    plain = stego.pack_payload(message, file_name, file_bytes)
    blob = crypto.encrypt(plain, password)
    try:
        res = stego.embed(carrier, blob, crypto.slot_seed(password), strength)
    except stego.StegoError as exc:
        raise HTTPException(422, str(exc)) from exc

    wav = stego.write_wav(res.audio)
    op_id = uuid.uuid4().hex
    out_name = f"{_safe_stem(audio.filename)}_waveflow.wav"
    out_path = config.STORAGE_DIR / f"{op_id}.wav"
    out_path.write_bytes(wav)

    diff = res.audio.samples[:, 0] - carrier.samples[:, 0]
    stealth = detector.analyze(res.audio)
    metrics = {
        "strength": res.strength, "repeats": res.repeats, "snr_db": res.snr_db,
        "fill_pct": res.fill_pct, "max_phase_shift_deg": res.max_phase_shift_deg,
        "bins_modified": int(res.slots_modified), "payload_bytes": len(plain), "encrypted_bytes": len(blob),
        "capacity_bytes": res.capacity_bytes, "duration": round(carrier.duration, 2),
        "sample_rate": carrier.sr, "notes": res.notes, "probability": stealth["probability"],
        "payload_kind": "file" if file_bytes is not None else "text",
    }
    _record(db, user, "encode", audio.filename or "audio.wav", metrics, out_path, out_name, op_id)
    return {
        "id": op_id, "metrics": metrics, "stealth": stealth, "output_name": out_name,
        "audio_url": f"/api/files/{op_id}", "download_url": f"/api/files/{op_id}?download=1",
        "peaks": {
            "original": stego.peaks(carrier.samples[:, 0]),
            "stego": stego.peaks(res.audio.samples[:, 0]),
            "difference": stego.peaks(diff),
        },
    }


@app.post("/api/decode")
def decode(
    audio: UploadFile = File(...),
    password: str = Form(...),
    user: User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    a = _load_audio(audio)
    generic = "No hidden message found. Check the password, and make sure this is the original WaveFlow WAV (not converted to MP3 or trimmed)."
    found = stego.extract(a, crypto.slot_seed(password))
    plain = None
    if found:
        try:
            plain = crypto.decrypt(found[0], password)
        except crypto.DecryptionError:
            plain = None
    if plain is None:
        _record(db, user, "decode", audio.filename or "audio.wav", {"ok": False})
        return JSONResponse({"found": False, "message": generic})

    payload = stego.unpack_payload(plain)
    meta = {"ok": True, "kind": payload["kind"], "strength": found[1], "repeats": found[2]}
    out_path = out_name = None
    op_id = uuid.uuid4().hex
    if payload["kind"] == "file":
        out_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", payload["filename"])[:120] or "payload.bin"
        out_path = config.STORAGE_DIR / f"{op_id}.bin"
        out_path.write_bytes(payload["bytes"])
        meta.update(filename=out_name, size=len(payload["bytes"]))
    else:
        meta.update(chars=len(payload["text"]))
    _record(db, user, "decode", audio.filename or "audio.wav", meta, out_path, out_name, op_id)
    resp = {"found": True, "kind": payload["kind"], "strength": found[1], "repeats": found[2]}
    if payload["kind"] == "text":
        resp["text"] = payload["text"]
    else:
        resp.update(filename=out_name, size=len(payload["bytes"]), note=payload["note"],
                    download_url=f"/api/files/{op_id}?download=1")
    return resp


@app.post("/api/analyze")
def analyze(audio: UploadFile = File(...), user: User = Depends(auth.get_current_user),
            db: Session = Depends(get_db)):
    a = _load_audio(audio)
    result = detector.analyze(a)
    _record(db, user, "analyze", audio.filename or "audio.wav",
            {"probability": result["probability"], "verdict": result["verdict"]})
    result["peaks"] = stego.peaks(a.samples[:, 0])
    return result


@app.get("/api/history")
def history(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    ops = (db.query(Operation).filter(Operation.user_id == user.id)
           .order_by(Operation.created_at.desc()).limit(60).all())
    return {"operations": [_op_public(o) for o in ops]}


@app.delete("/api/history/{op_id}")
def delete_op(op_id: str, user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    op = db.get(Operation, op_id)
    if not op or op.user_id != user.id:
        raise HTTPException(404, "Not found")
    if op.output_path:
        (config.STORAGE_DIR / op.output_path).unlink(missing_ok=True)
    db.delete(op)
    db.commit()
    return {"ok": True}


@app.get("/api/files/{op_id}")
def get_file(op_id: str, download: int = 0, user: User = Depends(auth.get_current_user),
             db: Session = Depends(get_db)):
    op = db.get(Operation, op_id)
    if not op or op.user_id != user.id or not op.output_path:
        raise HTTPException(404, "File not found")
    path = config.STORAGE_DIR / Path(op.output_path).name   # strip any path parts
    if not path.exists():
        raise HTTPException(404, "File no longer available")
    media = "audio/wav" if path.suffix == ".wav" else "application/octet-stream"
    return FileResponse(path, media_type=media, filename=op.output_name or path.name,
                        content_disposition_type="attachment" if download else "inline")


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
