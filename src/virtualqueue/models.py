import time
import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Service(Base):
    __tablename__ = "services"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(160))
    address: Mapped[str] = mapped_column(String(300))
    phone: Mapped[str] = mapped_column(String(30))
    latitude: Mapped[float] = mapped_column(Float, default=51.55)
    longitude: Mapped[float] = mapped_column(Float, default=0.07)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    feed_at: Mapped[float] = mapped_column(Float, default=0)
    feed_version: Mapped[int] = mapped_column(Integer, default=0)
    audit_seq: Mapped[int] = mapped_column(Integer, default=0)
    audit_head: Mapped[str] = mapped_column(String(64), default="0" * 64)
    callback_minutes: Mapped[int] = mapped_column(Integer, default=15)


class Slot(Base):
    __tablename__ = "slots"
    __table_args__ = (UniqueConstraint("service_id", "external_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(150))
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    capacity: Mapped[int] = mapped_column(Integer)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Visit(Base):
    __tablename__ = "visits"
    __table_args__ = (Index("ix_visits_service_state", "service_id", "state"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"), index=True)
    identity: Mapped[str] = mapped_column(Text)
    clinical: Mapped[str] = mapped_column(Text, default="")
    phone_hash: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(40), default="callback_requested")
    source: Mapped[str] = mapped_column(String(30), default="patient")
    priority: Mapped[int] = mapped_column(Integer, default=5)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    updated: Mapped[float] = mapped_column(Float, default=time.time)
    first_contact: Mapped[float] = mapped_column(Float, default=time.time)
    callback_due: Mapped[float] = mapped_column(Float)
    assessed_by: Mapped[str | None] = mapped_column(String(160))
    assessed_at: Mapped[float | None] = mapped_column(Float)
    approved_until: Mapped[float | None] = mapped_column(Float)
    review_due: Mapped[float | None] = mapped_column(Float)
    slot_id: Mapped[str | None] = mapped_column(ForeignKey("slots.id"))
    booking_reference: Mapped[str | None] = mapped_column(String(150))
    journey_minutes: Mapped[int] = mapped_column(Integer, default=20)
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=15)
    travel_mode: Mapped[str] = mapped_column(String(20), default="lift")
    travel_source: Mapped[str] = mapped_column(String(30), default="patient_estimate")
    travel_updated: Mapped[float] = mapped_column(Float, default=time.time)
    contact_needed: Mapped[bool] = mapped_column(Boolean, default=False)
    arrived_at: Mapped[float | None] = mapped_column(Float)
    treatment_at: Mapped[float | None] = mapped_column(Float)
    completed_at: Mapped[float | None] = mapped_column(Float)


class Audit(Base):
    __tablename__ = "audit"
    __table_args__ = (UniqueConstraint("service_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"), index=True)
    visit_id: Mapped[str | None] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(80))
    data: Mapped[str] = mapped_column(Text)
    created: Mapped[float] = mapped_column(Float)
    previous: Mapped[str] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64))


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("dedupe"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"), index=True)
    visit_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    dedupe: Mapped[str] = mapped_column(String(180))
    payload: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    due: Mapped[float] = mapped_column(Float, default=time.time)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(String(100))


class SessionRecord(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(30))
    tenant: Mapped[str] = mapped_column(String(80))
    visit_id: Mapped[str | None] = mapped_column(String(36))
    csrf: Mapped[str] = mapped_column(String(64))
    expires: Mapped[float] = mapped_column(Float, index=True)


class Challenge(Base):
    __tablename__ = "challenges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"))
    payload: Mapped[str] = mapped_column(Text)
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires: Mapped[float] = mapped_column(Float)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class AuthFlow(Base):
    __tablename__ = "auth_flows"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    verifier: Mapped[str] = mapped_column(Text)
    nonce: Mapped[str] = mapped_column(String(100))
    expires: Mapped[float] = mapped_column(Float)


class RateLimit(Base):
    __tablename__ = "rate_limits"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    expires: Mapped[float] = mapped_column(Float)
