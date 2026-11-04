"""
Database tables. Every table that holds business data carries ``org_id``, and every query goes through
``accounts.py`` / ``store.py``, which take the organisation from the authenticated session and never from request input.

Personal data kept, and why:
  users.email, users.name      to show who did what and to match invitations
  users.issuer, users.subject  the identity provider's stable account id (the real identity key)
Nothing else about a person is stored: no passwords (sign-in is delegated), no provider tokens, no IP addresses.
Session tokens are stored only as SHA-256 hashes.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Optional

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

ROLES = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)   # stored as naive UTC


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    loading_minutes: Mapped[float] = mapped_column(Float, default=0.0)   # handling time per pickup stop


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("issuer", "subject"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issuer: Mapped[str] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(300))
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    last_login_at: Mapped[Optional[dt.datetime]] = mapped_column(nullable=True)


class Membership(Base):
    __tablename__ = "memberships"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(10))
    invited_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[dt.datetime]
    accepted_at: Mapped[Optional[dt.datetime]] = mapped_column(nullable=True)


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)          # sha256 of the cookie token
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[dt.datetime]


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(64))
    code_verifier: Mapped[str] = mapped_column(String(128))
    next_path: Mapped[str] = mapped_column(String(300), default="/")
    expires_at: Mapped[dt.datetime]


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    org_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[str] = mapped_column(Text, default="")


class WarehouseRow(Base):
    __tablename__ = "warehouses"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    units_in_stock: Mapped[int] = mapped_column(Integer)
    vehicles_available: Mapped[int] = mapped_column(Integer)
    vehicle_capacity: Mapped[int] = mapped_column(Integer, default=200)


class OrderRow(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    destination_name: Mapped[str] = mapped_column(String(200))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer)
    deadline_hours: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12))
    plan_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    sources: Mapped[str] = mapped_column(Text, default="")
    eta_hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    p_late: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class Closure(Base):
    __tablename__ = "closures"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    until: Mapped[Optional[dt.datetime]] = mapped_column(nullable=True)


def make_session_factory(url: str):
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, pool_pre_ping=True)
    if url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _):  # SQLite ignores foreign keys unless asked
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False), engine
