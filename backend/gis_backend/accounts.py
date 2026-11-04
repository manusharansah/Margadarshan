"""
Accounts, organisations, roles, sessions, invitations, audit, order history and data-subject requests.

Rules this module enforces (and ``tests/test_accounts.py`` checks):
* Tenancy. Every read and write takes ``org_id`` from the authenticated session. No function accepts an
  organisation id from request input.
* Roles. viewer < member < admin < owner. Admins manage warehouses, closures and invitations to member/viewer.
  Only owners change roles, invite admins or owners, or delete the organisation. An organisation always keeps at
  least one owner.
* Sessions. The cookie holds a 256-bit random token; only its SHA-256 is stored. A session ends after
  ``session_idle_minutes`` without use or ``session_max_hours`` after sign-in, and when its user is removed.
* Sign-up. A new person can join only through an invitation addressed to their verified email, as the configured
  bootstrap owner (only while no organisation exists yet), or when self sign-up is explicitly enabled.
* Privacy. Export returns everything stored about a person. Deleting an account removes the person and their sessions
  and detaches their name from orders and audit entries (business records the organisation keeps).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, or_, select, update

from .config import Settings
from .db import (
    ROLES, AuditLog, Closure, Invite, LoginAttempt, Membership, OrderRow, Organization, SessionRow, User,
    WarehouseRow, utcnow,
)

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
INVITE_DAYS = 14
LOGIN_ATTEMPT_MINUTES = 10


class LoginDenied(Exception):
    """Sign-in succeeded at the identity provider but this person may not enter. ``code`` is safe to show in a URL."""

    def __init__(self, message: str, code: str = "denied"):
        super().__init__(message)
        self.code = code


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    user_id: str
    email: str
    name: str
    org_id: str
    org_name: str
    role: str

    def at_least(self, role: str) -> bool:
        return ROLES[self.role] >= ROLES[role]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _slug_name(email: str) -> str:
    domain = email.split("@", 1)[1].split(".")[0]
    return f"{domain.capitalize()} team" if domain not in {"gmail", "outlook", "yahoo", "hotmail", "icloud"} else "My organisation"


class Accounts:
    def __init__(self, session_factory, cfg: Settings):
        self._session, self.cfg = session_factory, cfg
        self._touch_cache: Dict[str, dt.datetime] = {}

    # ------------------------------------------------------------------ audit
    def audit(self, org_id: Optional[str], user_id: Optional[str], action: str, target: str = "",
              detail: Optional[dict] = None, s=None) -> None:
        row = AuditLog(org_id=org_id, user_id=user_id, action=action, target=target[:200],
                       detail=json.dumps(detail or {}, separators=(",", ":"))[:2000])
        if s is not None:
            s.add(row)
            return
        with self._session() as ss, ss.begin():
            ss.add(row)

    def list_audit(self, org_id: str, limit: int = 200) -> List[dict]:
        with self._session() as s:
            rows = s.scalars(select(AuditLog).where(AuditLog.org_id == org_id).order_by(AuditLog.id.desc()).limit(limit))
            emails = {u.id: u.email for u in s.scalars(select(User).join(Membership).where(Membership.org_id == org_id))}
            return [{"ts": r.ts.isoformat() + "Z", "user": emails.get(r.user_id or "", None), "action": r.action,
                     "target": r.target, "detail": json.loads(r.detail or "{}")} for r in rows]

    # ------------------------------------------------------------ login attempts
    def start_attempt(self, next_path: str, now: Optional[dt.datetime] = None) -> Tuple[str, str, str]:
        now = now or utcnow()
        state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        with self._session() as s, s.begin():
            s.execute(delete(LoginAttempt).where(LoginAttempt.expires_at < now))
            s.add(LoginAttempt(state=_hash(state), nonce=nonce, code_verifier=verifier, next_path=safe_next(next_path),
                               expires_at=now + dt.timedelta(minutes=LOGIN_ATTEMPT_MINUTES)))
        return state, nonce, verifier

    def consume_attempt(self, state: str, now: Optional[dt.datetime] = None) -> Optional[LoginAttempt]:
        """Single use: the attempt is deleted whether or not it is still valid."""
        now = now or utcnow()
        with self._session() as s, s.begin():
            row = s.get(LoginAttempt, _hash(state))
            if row is None:
                return None
            s.delete(row)
            return row if row.expires_at >= now else None

    # --------------------------------------------------------------- sign-in
    def provision_login(self, issuer: str, subject: str, email: str, name: str,
                        now: Optional[dt.datetime] = None) -> Tuple[str, bool]:
        """Find or create the user for a verified identity. Returns (user_id, created). Raises LoginDenied.
        A denial is audited outside the rolled-back transaction, so the record survives."""
        try:
            return self._provision(issuer, subject, email, name, now or utcnow())
        except LoginDenied as exc:
            self.audit(None, None, "login_denied", email_hash(email or ""), {"reason": str(exc)[:80]})
            raise

    def _provision(self, issuer: str, subject: str, email: str, name: str, now: dt.datetime) -> Tuple[str, bool]:
        email = email.strip().lower()
        if not EMAIL.match(email):
            raise LoginDenied("The identity provider returned an invalid email address.", "invalid_email")
        domains = self.cfg.allowed_email_domains
        if domains and email.split("@", 1)[1] not in domains:
            raise LoginDenied("Sign-in is limited to approved email domains.", "domain")
        with self._session() as s, s.begin():
            user = s.scalar(select(User).where(User.issuer == issuer, User.subject == subject))
            if user is not None:
                m = s.get(Membership, user.id)
                if m is None:                       # removed from the organisation earlier: a fresh invite may re-admit
                    m = self._join_by_invite(s, user, email, now)
                if m is None:
                    raise LoginDenied("Your account is not part of any organisation. Ask an owner to invite you.", "not_invited")
                user.email, user.name, user.last_login_at = email, (name or "")[:200], now
                self.audit(m.org_id, user.id, "login", "", s=s)
                return user.id, False
            user = User(id=str(uuid.uuid4()), issuer=issuer, subject=subject, email=email, name=(name or "")[:200],
                        created_at=now, last_login_at=now)
            s.add(user)
            s.flush()
            m = self._join_by_invite(s, user, email, now)
            first_ever = (s.scalar(select(func.count()).select_from(Organization)) or 0) == 0
            if m is None and ((email == self.cfg.bootstrap_owner_email and first_ever) or self.cfg.allow_self_signup):
                org = Organization(id=str(uuid.uuid4()), name=_slug_name(email), created_at=now)
                s.add(org)
                s.flush()
                m = Membership(user_id=user.id, org_id=org.id, role="owner", created_at=now)
                s.add(m)
                self.audit(org.id, user.id, "org_create", org.name, s=s)
            if m is None:
                raise LoginDenied("This email has not been invited. Ask an organisation owner to invite you.", "not_invited")
            self.audit(m.org_id, user.id, "login", "first sign-in", s=s)
            return user.id, True

    def _join_by_invite(self, s, user: User, email: str, now: dt.datetime) -> Optional[Membership]:
        inv = s.scalar(select(Invite).where(Invite.email == email, Invite.accepted_at.is_(None), Invite.expires_at > now)
                       .order_by(Invite.created_at.desc()))
        if inv is None:
            return None
        inv.accepted_at = now
        m = Membership(user_id=user.id, org_id=inv.org_id, role=inv.role, created_at=now)
        s.add(m)
        self.audit(inv.org_id, user.id, "invite_accept", inv.role, s=s)
        return m

    # -------------------------------------------------------------- sessions
    def create_session(self, user_id: str, now: Optional[dt.datetime] = None) -> str:
        now = now or utcnow()
        token = secrets.token_urlsafe(32)
        with self._session() as s, s.begin():
            s.add(SessionRow(id=_hash(token), user_id=user_id, created_at=now, last_seen_at=now,
                             expires_at=now + dt.timedelta(hours=self.cfg.session_max_hours)))
        return token

    def get_session(self, token: str, now: Optional[dt.datetime] = None) -> Optional[SessionInfo]:
        if not token or len(token) > 200:
            return None
        now = now or utcnow()
        sid = _hash(token)
        with self._session() as s, s.begin():
            row = s.get(SessionRow, sid)
            if row is None:
                return None
            idle = dt.timedelta(minutes=self.cfg.session_idle_minutes)
            if row.expires_at < now or now - row.last_seen_at > idle:
                s.delete(row)
                return None
            row2 = s.execute(select(User, Membership, Organization).join(Membership, Membership.user_id == User.id)
                             .join(Organization, Organization.id == Membership.org_id).where(User.id == row.user_id)).first()
            if row2 is None:                         # membership removed: the session dies with it
                s.delete(row)
                return None
            user, m, org = row2
            if now - row.last_seen_at > dt.timedelta(seconds=60):
                row.last_seen_at = now
            return SessionInfo(sid, user.id, user.email, user.name, org.id, org.name, m.role)

    def end_session(self, token: str) -> None:
        with self._session() as s, s.begin():
            s.execute(delete(SessionRow).where(SessionRow.id == _hash(token)))

    def end_user_sessions(self, user_id: str, s=None) -> None:
        stmt = delete(SessionRow).where(SessionRow.user_id == user_id)
        if s is not None:
            s.execute(stmt)
            return
        with self._session() as ss, ss.begin():
            ss.execute(stmt)

    def purge(self, now: Optional[dt.datetime] = None) -> dict:
        """Delete expired sessions, old orders and old audit entries according to the retention settings."""
        now = now or utcnow()
        with self._session() as s, s.begin():
            sess = s.execute(delete(SessionRow).where(or_(
                SessionRow.expires_at < now,
                SessionRow.last_seen_at < now - dt.timedelta(minutes=self.cfg.session_idle_minutes)))).rowcount
            orders = s.execute(delete(OrderRow).where(
                OrderRow.created_at < now - dt.timedelta(days=self.cfg.order_retention_days))).rowcount
            audit = s.execute(delete(AuditLog).where(
                AuditLog.ts < now - dt.timedelta(days=self.cfg.audit_retention_days))).rowcount
            s.execute(delete(Invite).where(Invite.expires_at < now - dt.timedelta(days=30)))
        return {"sessions": sess, "orders": orders, "audit": audit}

    # ---------------------------------------------------------------- team
    def list_members(self, org_id: str) -> List[dict]:
        with self._session() as s:
            rows = s.execute(select(User, Membership).join(Membership, Membership.user_id == User.id)
                             .where(Membership.org_id == org_id).order_by(User.email)).all()
            return [{"user_id": u.id, "email": u.email, "name": u.name, "role": m.role,
                     "last_login_at": u.last_login_at.isoformat() + "Z" if u.last_login_at else None} for u, m in rows]

    def list_invites(self, org_id: str, now: Optional[dt.datetime] = None) -> List[dict]:
        now = now or utcnow()
        with self._session() as s:
            rows = s.scalars(select(Invite).where(Invite.org_id == org_id, Invite.accepted_at.is_(None),
                                                  Invite.expires_at > now).order_by(Invite.created_at.desc()))
            return [{"id": i.id, "email": i.email, "role": i.role, "expires_at": i.expires_at.isoformat() + "Z"} for i in rows]

    def invite(self, actor: SessionInfo, email: str, role: str, now: Optional[dt.datetime] = None) -> dict:
        now = now or utcnow()
        email = email.strip().lower()
        if not EMAIL.match(email):
            raise ValueError("Enter a valid email address.")
        if role not in ROLES:
            raise ValueError("Unknown role.")
        if not actor.at_least("admin"):
            raise Forbidden("Only admins and owners can invite people.")
        if role in ("admin", "owner") and not actor.at_least("owner"):
            raise Forbidden("Only owners can invite admins or owners.")
        with self._session() as s, s.begin():
            s.execute(delete(Invite).where(Invite.org_id == actor.org_id, Invite.email == email, Invite.accepted_at.is_(None)))
            inv = Invite(id=str(uuid.uuid4()), org_id=actor.org_id, email=email, role=role, invited_by=actor.user_id,
                         created_at=now, expires_at=now + dt.timedelta(days=INVITE_DAYS))
            s.add(inv)
            self.audit(actor.org_id, actor.user_id, "invite_create", email_hash(email), {"role": role}, s=s)
            return {"id": inv.id, "email": email, "role": role, "expires_at": inv.expires_at.isoformat() + "Z"}

    def revoke_invite(self, actor: SessionInfo, invite_id: str) -> None:
        if not actor.at_least("admin"):
            raise Forbidden("Only admins and owners can revoke invitations.")
        with self._session() as s, s.begin():
            n = s.execute(delete(Invite).where(Invite.id == invite_id, Invite.org_id == actor.org_id)).rowcount
            if not n:
                raise NotFound("No such invitation.")
            self.audit(actor.org_id, actor.user_id, "invite_revoke", invite_id, s=s)

    def _owner_count(self, s, org_id: str) -> int:
        return s.scalar(select(func.count()).select_from(Membership).where(Membership.org_id == org_id, Membership.role == "owner")) or 0

    def change_role(self, actor: SessionInfo, user_id: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError("Unknown role.")
        if not actor.at_least("owner"):
            raise Forbidden("Only owners can change roles.")
        with self._session() as s, s.begin():
            m = s.get(Membership, user_id)
            if m is None or m.org_id != actor.org_id:
                raise NotFound("No such member.")
            if m.role == "owner" and role != "owner" and self._owner_count(s, actor.org_id) <= 1:
                raise Forbidden("An organisation needs at least one owner.")
            old, m.role = m.role, role
            self.audit(actor.org_id, actor.user_id, "role_change", user_id, {"from": old, "to": role}, s=s)

    def remove_member(self, actor: SessionInfo, user_id: str) -> None:
        if not actor.at_least("admin"):
            raise Forbidden("Only admins and owners can remove people.")
        with self._session() as s, s.begin():
            m = s.get(Membership, user_id)
            if m is None or m.org_id != actor.org_id:
                raise NotFound("No such member.")
            if ROLES[m.role] >= ROLES["admin"] and not actor.at_least("owner"):
                raise Forbidden("Only owners can remove admins and owners.")
            if m.role == "owner" and self._owner_count(s, actor.org_id) <= 1:
                raise Forbidden("An organisation needs at least one owner.")
            s.delete(m)
            self.end_user_sessions(user_id, s=s)
            self.audit(actor.org_id, actor.user_id, "member_remove", user_id, s=s)

    # --------------------------------------------------------------- orders
    def record_order(self, actor: SessionInfo, req: dict, result: dict) -> None:
        plan = result.get("chosen_plan")
        rel = (plan or {}).get("reliability") or {}
        with self._session() as s, s.begin():
            s.add(OrderRow(id=str(uuid.uuid4()), org_id=actor.org_id, user_id=actor.user_id,
                           destination_name=req["destination_name"][:200], lat=req["destination_lat"],
                           lng=req["destination_lng"], quantity=req["quantity"], deadline_hours=req["deadline_hours"],
                           status=result["status"], plan_type=(plan or {}).get("plan_type"),
                           sources=json.dumps((plan or {}).get("sources_used", [])),
                           eta_hours=(plan or {}).get("total_time_hours"), p_late=rel.get("p_late")))
            self.audit(actor.org_id, actor.user_id, "order_run", req["destination_name"][:80],
                       {"quantity": req["quantity"], "status": result["status"]}, s=s)

    def list_orders(self, org_id: str, limit: int = 100) -> List[dict]:
        with self._session() as s:
            rows = s.execute(select(OrderRow, User.email).outerjoin(User, User.id == OrderRow.user_id)
                             .where(OrderRow.org_id == org_id).order_by(OrderRow.created_at.desc()).limit(limit)).all()
            return [{"id": o.id, "created_at": o.created_at.isoformat() + "Z", "by": email, "destination": o.destination_name,
                     "lat": o.lat, "lng": o.lng, "quantity": o.quantity, "deadline_hours": o.deadline_hours,
                     "status": o.status, "plan_type": o.plan_type, "sources": json.loads(o.sources or "[]"),
                     "eta_hours": o.eta_hours, "p_late": o.p_late} for o, email in rows]

    # ------------------------------------------------------ closures, settings
    def add_closure(self, actor: SessionInfo, lat: float, lng: float, radius_m: float, reason: str,
                    until: Optional[dt.datetime] = None) -> dict:
        if not actor.at_least("admin"):
            raise Forbidden("Only admins and owners can record road closures.")
        if not (0 < radius_m <= 5000):
            raise ValueError("Radius must be between 1 and 5,000 metres.")
        with self._session() as s, s.begin():
            c = Closure(id=str(uuid.uuid4()), org_id=actor.org_id, lat=lat, lng=lng, radius_m=radius_m,
                        reason=reason[:200], created_by=actor.user_id, until=until)
            s.add(c)
            self.audit(actor.org_id, actor.user_id, "closure_add", reason[:80], {"radius_m": radius_m}, s=s)
            return self._closure_json(c)

    @staticmethod
    def _closure_json(c: Closure) -> dict:
        return {"id": c.id, "lat": c.lat, "lng": c.lng, "radius_m": c.radius_m, "reason": c.reason,
                "until": c.until.isoformat() + "Z" if c.until else None}

    def list_closures(self, org_id: str, now: Optional[dt.datetime] = None) -> List[dict]:
        now = now or utcnow()
        with self._session() as s:
            rows = s.scalars(select(Closure).where(Closure.org_id == org_id, or_(Closure.until.is_(None), Closure.until > now)))
            return [self._closure_json(c) for c in rows]

    def remove_closure(self, actor: SessionInfo, closure_id: str) -> None:
        if not actor.at_least("admin"):
            raise Forbidden("Only admins and owners can remove road closures.")
        with self._session() as s, s.begin():
            if not s.execute(delete(Closure).where(Closure.id == closure_id, Closure.org_id == actor.org_id)).rowcount:
                raise NotFound("No such closure.")
            self.audit(actor.org_id, actor.user_id, "closure_remove", closure_id, s=s)

    def get_settings(self, org_id: str) -> dict:
        with self._session() as s:
            o = s.get(Organization, org_id)
            return {"organisation": o.name, "loading_minutes": o.loading_minutes}

    def update_settings(self, actor: SessionInfo, loading_minutes: Optional[float] = None,
                        name: Optional[str] = None) -> dict:
        if not actor.at_least("admin"):
            raise Forbidden("Only admins and owners can change settings.")
        with self._session() as s, s.begin():
            o = s.get(Organization, actor.org_id)
            if loading_minutes is not None:
                if not (0 <= loading_minutes <= 480):
                    raise ValueError("Handling time must be between 0 and 480 minutes.")
                o.loading_minutes = loading_minutes
            if name:
                o.name = name.strip()[:200]
            self.audit(actor.org_id, actor.user_id, "settings_update", "", {"loading_minutes": loading_minutes}, s=s)
            return {"organisation": o.name, "loading_minutes": o.loading_minutes}

    # --------------------------------------------------- data-subject requests
    def export_user(self, actor: SessionInfo) -> dict:
        """Everything this system stores about the person (GDPR-style access request)."""
        with self._session() as s:
            u = s.get(User, actor.user_id)
            orders = s.scalars(select(OrderRow).where(OrderRow.user_id == u.id).order_by(OrderRow.created_at))
            audits = s.scalars(select(AuditLog).where(AuditLog.user_id == u.id).order_by(AuditLog.id))
            return {
                "user": {"id": u.id, "email": u.email, "name": u.name, "identity_provider": u.issuer,
                         "provider_subject": u.subject, "created_at": u.created_at.isoformat() + "Z",
                         "last_login_at": u.last_login_at.isoformat() + "Z" if u.last_login_at else None},
                "organisation": {"id": actor.org_id, "name": actor.org_name, "role": actor.role},
                "orders_placed": [{"created_at": o.created_at.isoformat() + "Z", "destination": o.destination_name,
                                   "quantity": o.quantity, "deadline_hours": o.deadline_hours, "status": o.status} for o in orders],
                "audit_entries": [{"ts": a.ts.isoformat() + "Z", "action": a.action, "target": a.target} for a in audits],
                "active_sessions": s.scalar(select(func.count()).select_from(SessionRow).where(SessionRow.user_id == u.id)),
                "note": "Sessions store only a hash of the cookie token. No passwords, provider tokens or IP addresses are stored.",
            }

    def delete_user(self, actor: SessionInfo) -> None:
        with self._session() as s, s.begin():
            m = s.get(Membership, actor.user_id)
            if m and m.role == "owner" and self._owner_count(s, m.org_id) <= 1:
                others = s.scalar(select(func.count()).select_from(Membership).where(
                    Membership.org_id == m.org_id, Membership.user_id != actor.user_id)) or 0
                if others:
                    raise Forbidden("You are the only owner. Make someone else an owner, or delete the organisation.")
            self.audit(m.org_id if m else None, None, "account_delete", email_hash(actor.email), s=s)
            s.execute(update(OrderRow).where(OrderRow.user_id == actor.user_id).values(user_id=None))
            s.execute(update(AuditLog).where(AuditLog.user_id == actor.user_id).values(user_id=None))
            s.execute(delete(SessionRow).where(SessionRow.user_id == actor.user_id))
            s.execute(delete(Membership).where(Membership.user_id == actor.user_id))
            s.execute(delete(User).where(User.id == actor.user_id))
            if m and not (s.scalar(select(func.count()).select_from(Membership).where(Membership.org_id == m.org_id)) or 0):
                self._delete_org_rows(s, m.org_id)     # last person out: the organisation's data goes too

    def delete_org(self, actor: SessionInfo, confirm_name: str) -> None:
        if not actor.at_least("owner"):
            raise Forbidden("Only owners can delete the organisation.")
        if confirm_name.strip() != actor.org_name:
            raise ValueError("Type the organisation name exactly to confirm.")
        with self._session() as s, s.begin():
            members = [m.user_id for m in s.scalars(select(Membership).where(Membership.org_id == actor.org_id))]
            self.audit(None, None, "org_delete", email_hash(actor.org_id), {"members": len(members)}, s=s)
            s.execute(delete(SessionRow).where(SessionRow.user_id.in_(members)))
            s.execute(delete(Membership).where(Membership.org_id == actor.org_id))
            s.execute(delete(User).where(User.id.in_(members)))
            self._delete_org_rows(s, actor.org_id)

    @staticmethod
    def _delete_org_rows(s, org_id: str) -> None:
        for model in (WarehouseRow, OrderRow, Closure, Invite, AuditLog):
            s.execute(delete(model).where(model.org_id == org_id))
        s.execute(delete(Organization).where(Organization.id == org_id))


def email_hash(email: str) -> str:
    """Audit entries for people who may not have an account keep only a short hash of the email."""
    return "h:" + hashlib.sha256(email.strip().lower().encode()).hexdigest()[:12]


def safe_next(path: str) -> str:
    """Only same-site absolute paths may be used as a post-login destination (no open redirect)."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path or "\n" in path or len(path) > 300:
        return "/"
    return path
