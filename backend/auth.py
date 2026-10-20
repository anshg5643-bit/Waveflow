"""Authentication.

Google Sign-In flow (Google Identity Services):
  browser shows Google button -> Google returns a signed ID token (JWT)
  -> POST /api/auth/google -> we verify signature + audience + expiry with
  google-auth -> upsert the user -> set our own httpOnly session cookie.
The Google token is never trusted client-side and never stored.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
import secrets

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from google.auth import exceptions as g_exc
from google.auth.transport import requests as g_requests
from google.oauth2 import id_token
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import config
from .database import User, UserCredential, get_db, utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])
COOKIE = "wf_session"


class GoogleBody(BaseModel):
    credential: str


class AccountBody(BaseModel):
    name: str = ""
    email: str
    password: str


class LoginBody(BaseModel):
    email: str
    password: str


def _password_hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex()


def _normal_email(email: str) -> str:
    return email.strip().lower()


def verify_google_token(credential: str) -> dict:
    """Verify a Google ID token. Separated out so tests can stub it."""
    if not config.GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google Sign-In is not configured. Set GOOGLE_CLIENT_ID in .env.")
    try:
        info = id_token.verify_oauth2_token(credential, g_requests.Request(), config.GOOGLE_CLIENT_ID)
    except ValueError as exc:
        raise HTTPException(401, f"Invalid Google token: {exc}") from exc
    except g_exc.TransportError as exc:
        raise HTTPException(503, "Could not reach Google to verify the sign-in. Try again.") from exc
    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise HTTPException(401, "Invalid token issuer")
    if not info.get("email_verified", False):
        raise HTTPException(401, "Google account email is not verified")
    return info


def _issue(response: Response, user: User) -> None:
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=config.SESSION_DAYS)
    token = jwt.encode({"sub": str(user.id), "exp": exp}, config.JWT_SECRET, algorithm="HS256")
    response.set_cookie(
        COOKIE, token, max_age=config.SESSION_DAYS * 86400, httponly=True,
        samesite="lax", secure=config.COOKIE_SECURE, path="/",
    )


def user_public(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "picture": u.picture, "guest": bool(u.is_guest)}


def get_current_user(wf_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> User:
    if not wf_session:
        raise HTTPException(401, "Not signed in")
    try:
        data = jwt.decode(wf_session, config.JWT_SECRET, algorithms=["HS256"])
        user = db.get(User, int(data["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Session expired - sign in again") from None
    if not user:
        raise HTTPException(401, "Unknown user")
    return user


@router.post("/register")
def register(body: AccountBody, response: Response, db: Session = Depends(get_db)):
    email = _normal_email(body.email)
    name = body.name.strip()[:100] or email.split("@")[0]
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(400, "Enter a valid email address.")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if len(body.password) > 256:
        raise HTTPException(400, "Password is too long.")
    if db.query(UserCredential).filter(UserCredential.email == email).first():
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    # Avoid linking a password account to an existing Google account by email.
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "This email is already associated with an account. Try Google sign-in.")
    salt = secrets.token_bytes(16)
    user = User(name=name, email=email, is_guest=0, last_login=utcnow())
    db.add(user)
    db.flush()
    cred = UserCredential(user_id=user.id, email=email, salt=salt.hex(), password_hash=_password_hash(body.password, salt))
    db.add(cred)
    db.commit()
    _issue(response, user)
    return {"user": user_public(user)}


@router.post("/login")
def password_login(body: LoginBody, response: Response, db: Session = Depends(get_db)):
    email = _normal_email(body.email)
    cred = db.query(UserCredential).filter(UserCredential.email == email).first()
    if not cred:
        raise HTTPException(401, "Email or password is incorrect.")
    try:
        salt = bytes.fromhex(cred.salt)
        valid = hmac.compare_digest(_password_hash(body.password, salt), cred.password_hash)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(401, "Email or password is incorrect.")
    user = db.get(User, cred.user_id)
    if not user:
        raise HTTPException(401, "Account not found. Please create a new account.")
    user.last_login = utcnow()
    db.commit()
    _issue(response, user)
    return {"user": user_public(user)}


@router.post("/google")
def google_login(body: GoogleBody, response: Response, db: Session = Depends(get_db)):
    info = verify_google_token(body.credential)
    user = db.query(User).filter(User.google_sub == info["sub"]).one_or_none()
    if user is None:
        user = User(google_sub=info["sub"])
        db.add(user)
    user.email = info.get("email", "")
    user.name = info.get("name") or info.get("email", "").split("@")[0]
    user.picture = info.get("picture", "")
    user.last_login = utcnow()
    db.commit()
    _issue(response, user)
    return {"user": user_public(user)}


@router.post("/guest")
def guest_login(response: Response, db: Session = Depends(get_db)):
    if not config.ALLOW_GUEST_LOGIN:
        raise HTTPException(403, "Guest access is disabled")
    user = User(name="Guest", email="", is_guest=1)
    db.add(user)
    db.commit()
    user.name = f"Guest {user.id}"
    db.commit()
    _issue(response, user)
    return {"user": user_public(user)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"user": user_public(user)}
