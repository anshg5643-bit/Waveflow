"""Root ASGI entry point for WaveFlow.

Run from the project root with:
    python -m uvicorn app:app --reload

The actual FastAPI application and routes are implemented in backend.main.
"""
from backend.main import app

__all__ = ["app"]
