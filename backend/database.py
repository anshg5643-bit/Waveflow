"""SQLite (default) via SQLAlchemy 2.0. Swap DATABASE_URL for Postgres in production."""
from __future__ import annotations

import datetime as dt
import json
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    name: Mapped[str] = mapped_column(String(255), default="")
    picture: Mapped[str] = mapped_column(String(512), default="")
    is_guest: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    last_login: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    operations: Mapped[list["Operation"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserCredential(Base):
    """Credentials for email/password accounts; password material is salted and hashed."""
    __tablename__ = "user_credentials"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    salt: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Operation(Base):
    """One encode / decode / analyze run. `meta` holds JSON metrics."""
    __tablename__ = "operations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))             # encode | decode | analyze
    input_name: Mapped[str] = mapped_column(String(255), default="")
    output_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    output_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)

    user: Mapped[User] = relationship(back_populates="operations")

    def meta_dict(self) -> dict:
        try:
            return json.loads(self.meta)
        except ValueError:
            return {}


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(12))              # user | assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="messages")


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
