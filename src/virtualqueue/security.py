import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select

from .models import RateLimit, SessionRecord


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class Vault:
    def __init__(self, settings):
        self.fernet = Fernet(settings.encryption_key.encode())
        self.key = settings.signing_key.encode()

    def seal(self, value):
        return self.fernet.encrypt(canonical(value).encode()).decode()

    def open(self, value):
        return json.loads(self.fernet.decrypt(value.encode())) if value else {}

    def digest(self, value):
        return hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Actor:
    subject: str
    role: str
    tenant: str
    visit_id: str | None = None
    csrf: str = ""

    def require(self, service, roles):
        if self.role not in roles or self.tenant != service.tenant:
            raise HTTPException(403, "This account cannot perform that action")


def issue_session(session, vault, settings, subject, role, tenant, visit_id=None, expires=None):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    session.add(SessionRecord(token_hash=vault.digest(token), subject=subject, role=role,
                              tenant=tenant, visit_id=visit_id, csrf=csrf,
                              expires=min(expires or float("inf"), time.time() + settings.session_seconds)))
    return token, csrf


def rate_limit(session, vault, key, limit, period, now=None):
    now = now or time.time()
    hashed = vault.digest(key)
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
        session.execute(insert(RateLimit).values(key=hashed, count=0, expires=now + period)
                        .on_conflict_do_nothing(index_elements=["key"]))
    row = session.execute(select(RateLimit).where(RateLimit.key == hashed).with_for_update()).scalar_one_or_none()
    if not row:
        row = RateLimit(key=hashed, count=0, expires=now + period)
        session.add(row)
    if row.expires <= now:
        row.count, row.expires = 0, now + period
    if row.count >= limit:
        raise HTTPException(429, "Too many requests. Please try again later.")
    row.count += 1


def set_session_cookie(response, token, settings):
    response.set_cookie("vq_session", token, httponly=True, secure=settings.mode == "production",
                        samesite="strict", max_age=settings.session_seconds, path="/")
