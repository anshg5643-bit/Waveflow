import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Optional local secrets file, in KEY=value format. Environment variables take priority.
def _load_api_keys_file() -> dict[str, str]:
    path = ROOT / "api_keys.txt"
    values: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values

_API_KEYS = _load_api_keys_file()
def _secret(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or _API_KEYS.get(name, default).strip()

log = logging.getLogger("waveflow")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
STORAGE_DIR = DATA_DIR / "files"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'waveflow.db'}")

GOOGLE_CLIENT_ID = _secret("GOOGLE_CLIENT_ID")
GROQ_API_KEY = _secret("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", _API_KEYS.get("GROQ_MODEL", "llama-3.3-70b-versatile")).strip()
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")

ALLOW_GUEST_LOGIN = _bool("ALLOW_GUEST_LOGIN", True)
COOKIE_SECURE = _bool("COOKIE_SECURE", False)     # set true behind HTTPS
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "40"))
MAX_AUDIO_SECONDS = int(os.getenv("MAX_AUDIO_SECONDS", "300"))
SESSION_DAYS = 7

JWT_SECRET = _secret("JWT_SECRET")
if not JWT_SECRET:
    JWT_SECRET = secrets.token_urlsafe(48)
    log.warning("JWT_SECRET not set - using a random one. Sessions reset on every restart.")

DATA_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
