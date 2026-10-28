import logging
import time

from sqlalchemy import delete, select

from .adapters import TrustGateway
from .domain import (
    REMOTE,
    TERMINAL,
    accept_booking,
    audit,
    escalate,
    finish_cancellation,
    release_slot,
    service_for,
    sweep_service,
)
from .models import AuthFlow, Challenge, Job, RateLimit, Service, SessionRecord, Visit

log = logging.getLogger(__name__)


def tick(db, vault, settings, gateway=None, now=None):
    gateway, now = gateway or TrustGateway(settings), now or time.time()
    with db.transaction() as session:
        for model in (AuthFlow, SessionRecord, RateLimit):
            session.execute(delete(model).where(model.expires < now))
    with db.read() as session:
        service_ids = list(session.scalars(select(Service.id)))
    for service_id in service_ids:
        with db.transaction(service_id) as session:
            service = service_for(session, service_id)
            session.execute(delete(Challenge).where(Challenge.service_id == service_id, Challenge.expires < now))
            for old in session.scalars(select(Job).where(Job.service_id == service_id,
                                       Job.kind == "verification_code", Job.created < now - 360)):
                old.payload = vault.seal({"redacted": True})
                if old.state == "pending":
                    old.state = "cancelled"
            sweep_service(session, vault, settings, service, now)
            for job in session.scalars(select(Job).where(Job.service_id == service_id,
                                      Job.kind == "notification", Job.state == "accepted",
                                      Job.claimed_at < now - 180)):
                job.state, job.error = "unknown", "delivery_receipt_overdue"
                visit = session.get(Visit, job.visit_id)
                if visit and visit.state not in TERMINAL and not visit.contact_needed:
                    escalate(session, vault, service, visit, "worker", "No delivery receipt; telephone follow-up required", now)
            for job in session.scalars(select(Job).where(Job.service_id == service_id,
                                      Job.state == "sending", Job.claimed_at < now - 60)):
                job.state, job.error = "unknown", "worker_interrupted"
                if job.visit_id:
                    visit = session.get(Visit, job.visit_id)
                    if visit.state not in TERMINAL and not visit.contact_needed:
                        escalate(session, vault, service, visit, "worker", "Delivery outcome unknown", now)
            # Claim under the same service lock; only one worker can claim a job.
            ids = list(session.scalars(select(Job.id).where(Job.service_id == service_id,
                                      Job.state == "pending", Job.due <= now).order_by(Job.created).limit(30)))
        for job_id in ids:
            with db.transaction(service_id) as session:
                job = session.get(Job, job_id)
                service = service_for(session, service_id)
                if job.state != "pending":
                    continue
                payload = vault.open(job.payload)
                visit = session.get(Visit, job.visit_id) if job.visit_id else None
                purpose = payload.get("purpose")
                if job.kind == "verification_code" and payload["expires"] <= now:
                    job.state = "cancelled"
                    continue
                if job.kind == "booking" and (visit.state != "booking_pending" or visit.version != payload["version"]):
                    job.state = "cancelled"
                    if not visit.booking_reference:
                        release_slot(session, visit)
                    if visit.state == "booking_pending":
                        escalate(session, vault, service, visit, "worker", "Request changed before dispatch; review booking", now)
                    continue
                if job.kind == "notification" and purpose in {"arrival_confirmed", "departure_reminder"}:
                    if visit.state not in REMOTE or visit.version != payload["version"]:
                        job.state = "cancelled"
                        continue
                job.state, job.claimed_at = "sending", now
                job.attempts += 1
            try:
                outcome = gateway.send(job, payload)
            except Exception:
                # Never log payloads, credentials, patient IDs, response bodies or phone numbers.
                outcome = {"status": "unknown"}
                log.warning("Gateway outcome unknown; staff reconciliation required")
            with db.transaction(service_id) as session:
                service, job = service_for(session, service_id), session.get(Job, job_id)
                visit = session.get(Visit, job.visit_id) if job.visit_id else None
                status = outcome["status"]
                if job.kind == "booking" and status == "accepted":
                    accept_booking(session, vault, service, visit, job, outcome["reference"], now)
                elif job.kind == "cancel_booking" and status in {"accepted", "simulated"}:
                    finish_cancellation(session, vault, service, visit, job, now)
                elif job.kind == "booking" and status == "rejected":
                    job.state = "rejected"
                    release_slot(session, visit)
                    if visit.state not in TERMINAL | {"arrived", "in_care"}:
                        escalate(session, vault, service, visit, "integration", "Receiving service declined the booking", now)
                    else:
                        audit(session, vault, service, "integration", "late_booking_rejected", visit, now=now)
                elif status in {"accepted", "delivered", "simulated"}:
                    job.state = status
                    audit(session, vault, service, "worker", "delivery_" + status, visit,
                          {"kind": job.kind, "job_id": job.id}, now)
                else:
                    job.state, job.error = "unknown", "provider_outcome_unconfirmed"
                    if visit and visit.state not in TERMINAL and not visit.contact_needed:
                        escalate(session, vault, service, visit, "integration", "Delivery requires staff follow-up", now)


def run(db, vault, settings):
    while True:
        try:
            tick(db, vault, settings)
        except Exception:
            log.error("Worker cycle failed; inspect service health")
        time.sleep(10)
