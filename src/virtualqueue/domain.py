import hmac
import time

from fastapi import HTTPException
from sqlalchemy import select

from .models import Audit, Job, Service, Slot, Visit, uid
from .security import canonical

TERMINAL = {"completed", "closed_after_review"}
REMOTE = {"remote_waiting", "called_in", "travelling"}
CLINICAL = {"clinician"}
OPERATORS = {"clinician", "coordinator", "admin"}


def service_for(session, service_id):
    service = session.get(Service, service_id)
    if not service:
        raise HTTPException(404, "Service not found")
    return service


def visit_for(session, visit_id, service_id=None):
    visit = session.get(Visit, visit_id)
    if not visit or (service_id and visit.service_id != service_id):
        raise HTTPException(404, "Request not found")
    return visit


def check_version(visit, expected):
    if visit.version != expected:
        raise HTTPException(409, "This record changed. Refresh before trying again.")


def touch(visit, now):
    visit.version += 1
    visit.updated = now


def audit(session, vault, service, actor, kind, visit=None, details=None, now=None):
    now = now or time.time()
    service.audit_seq += 1
    data = vault.seal(details or {})
    envelope = [service.id, service.audit_seq, actor, kind, visit.id if visit else None,
                now, service.audit_head, data]
    digest = vault.digest(canonical(envelope))
    session.add(Audit(service_id=service.id, sequence=service.audit_seq, actor=actor,
                      kind=kind, visit_id=visit.id if visit else None, created=now,
                      previous=service.audit_head, digest=digest, data=data))
    service.audit_head = digest


def verify_audit(session, vault, service):
    previous, count = "0" * 64, 0
    for event in session.scalars(select(Audit).where(Audit.service_id == service.id).order_by(Audit.sequence)):
        count += 1
        envelope = [service.id, event.sequence, event.actor, event.kind, event.visit_id,
                    event.created, event.previous, event.data]
        if event.sequence != count or event.previous != previous or not hmac.compare_digest(
            event.digest, vault.digest(canonical(envelope))
        ):
            return False
        previous = event.digest
    return count == service.audit_seq and previous == service.audit_head


def enqueue(session, vault, service, kind, payload, visit=None, dedupe=None, due=None):
    identifier = uid()
    job = Job(id=identifier, service_id=service.id, visit_id=visit.id if visit else None,
              kind=kind, payload=vault.seal(payload), dedupe=dedupe or identifier,
              due=due or time.time())
    session.add(job)
    return job


def notice(session, vault, service, visit, purpose, due=None):
    return enqueue(session, vault, service, "notification",
                   {"phone": vault.open(visit.identity)["phone"], "purpose": purpose,
                    "visit_id": visit.id, "version": visit.version}, visit,
                   dedupe=f"{visit.id}:{visit.version}:{purpose}", due=due)


def release_slot(session, visit):
    if visit.slot_id:
        slot = session.get(Slot, visit.slot_id)
        if slot:
            slot.reserved = max(0, slot.reserved - 1)
        visit.slot_id = None


def escalate(session, vault, service, visit, actor, reason, now=None):
    now = now or time.time()
    if visit.state in TERMINAL:
        raise HTTPException(409, "This episode is closed. Contact the care service for new symptoms.")
    if visit.state not in {"arrived", "in_care"}:
        visit.state = "reassessment_required"
    visit.contact_needed = True
    touch(visit, now)
    audit(session, vault, service, actor, "reassessment_required", visit, {"reason": reason}, now)
    notice(session, vault, service, visit, "review_required")
    # Keep the slot reserved until staff have reviewed the episode. No automatic discharge.


def create_visit(session, vault, service, identity, source="patient", actor="patient", now=None):
    now = now or time.time()
    if service.paused or now - service.feed_at > 300:
        raise HTTPException(409, "This service cannot accept online requests. Contact NHS 111 or the care team.")
    phone_hash = vault.digest(identity["phone"])
    active = session.scalar(select(Visit).where(Visit.service_id == service.id,
                                              Visit.phone_hash == phone_hash,
                                              Visit.state.not_in(TERMINAL)))
    if active:
        raise HTTPException(409, "An active request already exists. Use your reference to reopen it.")
    visit = Visit(id=uid(), service_id=service.id, identity=vault.seal(identity), phone_hash=phone_hash,
                  state="callback_requested", source=source, created=now, updated=now,
                  first_contact=now, callback_due=now + service.callback_minutes * 60,
                  version=1, priority=5, journey_minutes=20, buffer_minutes=15,
                  travel_mode="lift", travel_source="patient_estimate", travel_updated=now,
                  contact_needed=False)
    session.add(visit)
    audit(session, vault, service, actor, "callback_requested", visit, {"source": source}, now)
    return visit


def assess(session, vault, settings, service, visit, actor, data, now=None):
    now = now or time.time()
    actor.require(service, CLINICAL)
    check_version(visit, data.version)
    if visit.state not in {"assessment_in_progress", "reassessment_required"}:
        raise HTTPException(409, "Start a clinical assessment before recording a decision")
    if visit.slot_id or visit.booking_reference:
        raise HTTPException(409, "Resolve the existing booking with the receiving service before a new booking")
    review, deadline = data.review_due.timestamp(), data.approved_until.timestamp()
    if not now < review <= deadline <= now + 86400:
        raise HTTPException(422, "Review and attendance limits must be in the next 24 hours, in that order")
    if data.decision == "remote_wait":
        if data.priority < 3:
            raise HTTPException(422, "Priority 1 and 2 patients cannot enter remote waiting")
        if service.paused or now - service.feed_at > settings.feed_ttl_seconds:
            raise HTTPException(409, "Capacity information is unavailable or stale")
        slot = session.get(Slot, data.slot_id or "")
        if not slot or slot.service_id != service.id or not slot.enabled:
            raise HTTPException(422, "Select an available slot for this care service")
        if slot.reserved >= slot.capacity:
            raise HTTPException(409, "That arrival window is full")
        if slot.start < now + (visit.journey_minutes + visit.buffer_minutes) * 60:
            raise HTTPException(422, "The journey does not fit this arrival window")
        if slot.end > deadline:
            raise HTTPException(422, "The arrival window extends beyond the clinical attendance limit")
        slot.reserved += 1
        visit.slot_id = slot.id
    visit.assessed_by, visit.assessed_at = actor.subject, now
    visit.approved_until, visit.review_due, visit.priority = deadline, review, data.priority
    visit.clinical = vault.seal(data.model_dump(mode="json"))
    visit.state = "booking_pending" if data.decision == "remote_wait" else "attend_now"
    visit.contact_needed = False
    touch(visit, now)
    audit(session, vault, service, actor.subject, "assessment_recorded", visit,
          {"decision": data.decision, "assessment_reference": data.assessment_reference}, now)
    if data.decision == "remote_wait":
        enqueue(session, vault, service, "booking", {
            "visit_id": visit.id, "version": visit.version,
            "slot_reference": slot.external_id, "start": slot.start, "end": slot.end,
            "assessment_reference": data.assessment_reference,
            "patient": vault.open(visit.identity),
        }, visit)
    else:
        notice(session, vault, service, visit, "attend_now")


def accept_booking(session, vault, service, visit, job, external_reference, now=None):
    now = now or time.time()
    payload = vault.open(job.payload)
    visit.booking_reference = external_reference
    if visit.state in TERMINAL | {"arrived", "in_care"}:
        # A late integration response must never undo recorded clinical care.
        visit.contact_needed = True
        touch(visit, now)
        job.state = "accepted"
        audit(session, vault, service, "integration", "late_booking_requires_reconciliation", visit,
              {"booking_reference": external_reference}, now)
        return
    if (visit.state != "booking_pending" or visit.version != payload["version"]
            or not visit.review_due or visit.review_due <= now
            or not visit.approved_until or visit.approved_until <= now):
        escalate(session, vault, service, visit, "integration", "Booking accepted after eligibility changed", now)
        job.state = "accepted"
        return
    slot = session.get(Slot, visit.slot_id)
    if not slot or slot.start < now + (visit.journey_minutes + visit.buffer_minutes) * 60:
        escalate(session, vault, service, visit, "integration", "Booking response left insufficient travel time", now)
    else:
        visit.state = "remote_waiting"
        touch(visit, now)
        audit(session, vault, service, "integration", "arrival_confirmed", visit,
              {"booking_reference": external_reference}, now)
        notice(session, vault, service, visit, "arrival_confirmed")
        notice(session, vault, service, visit, "departure_reminder",
               due=slot.start - (visit.journey_minutes + visit.buffer_minutes) * 60)
    job.state = "accepted"


def transition(session, vault, service, visit, actor, data, now=None):
    now = now or time.time()
    check_version(visit, data.version)
    action = data.action
    actor.require(service, OPERATORS if action == "arrived" else CLINICAL)
    if action == "cancel_booking":
        if not visit.booking_reference or visit.state not in REMOTE | {"reassessment_required", "assessment_in_progress"}:
            raise HTTPException(409, "No confirmed booking is available to cancel")
        enqueue(session, vault, service, "cancel_booking", {"booking_reference": visit.booking_reference,
                "next_state": "reassessment_required", "version": visit.version + 1}, visit)
        visit.state, visit.contact_needed = "cancellation_pending", True
        touch(visit, now)
        audit(session, vault, service, actor.subject, "cancellation_requested", visit, {"reason": data.reason}, now)
        return
    allowed = {"start_assessment": ({"callback_requested", "reassessment_required"}, "assessment_in_progress"),
               "arrived": (REMOTE | {"attend_now", "reassessment_required", "booking_pending"}, "arrived"),
               "start_care": ({"arrived"}, "in_care"), "complete": ({"in_care"}, "completed"),
               "close_after_review": ({"callback_requested", "assessment_in_progress", "reassessment_required",
                                        "attend_now"} | REMOTE, "closed_after_review")}
    if action == "recall":
        escalate(session, vault, service, visit, actor.subject, data.reason, now)
        return
    states, target = allowed[action]
    if visit.state not in states:
        raise HTTPException(409, "That action is not valid for the current state")
    if action == "close_after_review":
        unresolved = session.scalar(select(Job).where(Job.visit_id == visit.id, Job.kind == "booking",
                                    Job.state.in_({"sending", "unknown"})))
        if unresolved:
            raise HTTPException(409, "Reconcile the unknown booking before closing this episode")
        if visit.booking_reference:
            enqueue(session, vault, service, "cancel_booking", {"booking_reference": visit.booking_reference,
                    "next_state": "closed_after_review", "version": visit.version + 1}, visit)
            target = "closure_pending"
    visit.state = target
    visit.contact_needed = False
    if action == "arrived":
        visit.arrived_at = now
    if action == "start_care":
        visit.treatment_at = now
    if target in TERMINAL:
        visit.completed_at = now
        release_slot(session, visit)
    touch(visit, now)
    audit(session, vault, service, actor.subject, target, visit, {"reason": data.reason}, now)


def finish_cancellation(session, vault, service, visit, job, now=None):
    now = now or time.time()
    payload = vault.open(job.payload)
    release_slot(session, visit)
    visit.booking_reference = None
    if visit.state in {"cancellation_pending", "closure_pending"} and visit.version == payload.get("version"):
        visit.state = payload.get("next_state", "reassessment_required")
    elif visit.state not in TERMINAL | {"arrived", "in_care"}:
        visit.state = "reassessment_required"
    visit.contact_needed = visit.state == "reassessment_required" or (
        visit.state in {"arrived", "in_care"} and visit.contact_needed)
    if visit.state in TERMINAL:
        visit.completed_at = now
    touch(visit, now)
    job.state = "accepted"
    audit(session, vault, service, "integration", "booking_cancelled", visit, now=now)


def apply_feed(session, vault, service, actor, data, now=None):
    now = now or time.time()
    actor.require(service, {"integration"})
    if data.version <= service.feed_version:
        raise HTTPException(409, "Feed version is stale or already processed")
    observed = data.observed_at.timestamp()
    if not now - 300 <= observed <= now + 30:
        raise HTTPException(422, "Feed observation time is outside the accepted window")
    if len({s.external_id for s in data.slots}) != len(data.slots):
        raise HTTPException(422, "Duplicate slot references")
    for item in data.slots:
        if item.end <= item.start:
            raise HTTPException(422, "Slot end must follow its start")
    known = {s.external_id: s for s in session.scalars(select(Slot).where(Slot.service_id == service.id))}
    for old in known.values():
        old.enabled = False
    for item in data.slots:
        slot = known.get(item.external_id)
        if slot:
            if slot.reserved and (slot.start != item.start.timestamp() or slot.end != item.end.timestamp()):
                raise HTTPException(409, "A reserved arrival window cannot be moved by a capacity feed")
            slot.capacity, slot.enabled = item.capacity, item.enabled
            if not slot.reserved:
                slot.start, slot.end = item.start.timestamp(), item.end.timestamp()
        else:
            session.add(Slot(service_id=service.id, external_id=item.external_id,
                             start=item.start.timestamp(), end=item.end.timestamp(), capacity=item.capacity,
                             enabled=item.enabled))
    service.feed_at, service.feed_version, service.paused = observed, data.version, data.paused
    audit(session, vault, service, actor.subject, "capacity_updated", details={"version": data.version}, now=now)
    for visit in session.scalars(select(Visit).where(Visit.service_id == service.id,
                                                   Visit.state.in_(REMOTE | {"booking_pending"}))):
        slot = session.get(Slot, visit.slot_id) if visit.slot_id else None
        if service.paused or not slot or not slot.enabled or slot.reserved > slot.capacity:
            escalate(session, vault, service, visit, "integration", "Hospital capacity changed; clinical review needed", now)


def public_visit(session, vault, settings, service, visit, now=None):
    now = now or time.time()
    slot = session.get(Slot, visit.slot_id) if visit.slot_id else None
    fresh = now - service.feed_at <= settings.feed_ttl_seconds and not service.paused
    valid = bool(fresh and visit.state in REMOTE and visit.review_due and now < visit.review_due
                 and visit.approved_until and now < visit.approved_until and slot and slot.enabled and slot.end > now)
    peers = []
    if fresh and valid:
        peers = list(session.scalars(select(Visit).where(Visit.service_id == service.id,
                        Visit.state.in_(REMOTE | {"arrived"}), Visit.id != visit.id)))
    ahead = sum((p.priority, p.created) < (visit.priority, visit.created) for p in peers) if fresh and valid else None
    clinical = vault.open(visit.clinical)
    history = list(session.scalars(select(Audit).where(Audit.visit_id == visit.id).order_by(Audit.sequence)))
    return {"id": visit.id, "state": visit.state, "version": visit.version,
            "name": vault.open(visit.identity).get("name", ""),
            "service": {"id": service.id, "name": service.name, "address": service.address, "phone": service.phone},
            "approximately_ahead": ahead, "capacity_updated_at": service.feed_at,
            "estimate_available": fresh and valid, "updated_at": visit.updated,
            "arrival_window": {"start": slot.start, "end": slot.end} if valid else None,
            "leave_by": slot.start - (visit.journey_minutes + visit.buffer_minutes) * 60 if valid else None,
            "journey_minutes": visit.journey_minutes, "buffer_minutes": visit.buffer_minutes,
            "travel_mode": visit.travel_mode, "travel_source": visit.travel_source,
            "travel_updated_at": visit.travel_updated, "review_due": visit.review_due,
            "callback_due": visit.callback_due, "contact_needed": visit.contact_needed,
            "safety_net": clinical.get("safety_net", "If you need urgent help, contact NHS 111. For a life-threatening emergency call 999."),
            "timeline": [{"kind": e.kind, "at": e.created} for e in history
                         if e.kind not in {"patient_record_viewed", "staff_record_viewed"}],
            "warning": "An arrival window is not a guaranteed treatment time. Clinical urgency determines priority."}


def staff_visit(session, vault, settings, service, visit, include_clinical=True):
    result = public_visit(session, vault, settings, service, visit)
    result.update(identity=vault.open(visit.identity), clinical=vault.open(visit.clinical) if include_clinical else {},
                  priority=visit.priority, source=visit.source, created=visit.created,
                  assessed_by=visit.assessed_by, approved_until=visit.approved_until,
                  booking_reference=visit.booking_reference, slot_id=visit.slot_id)
    return result


def sweep_service(session, vault, settings, service, now=None):
    now = now or time.time()
    visits = session.scalars(select(Visit).where(Visit.service_id == service.id, Visit.state.not_in(TERMINAL)))
    for visit in visits:
        if visit.state == "callback_requested" and visit.callback_due < now and not visit.contact_needed:
            visit.contact_needed = True
            touch(visit, now)
            audit(session, vault, service, "watchdog", "callback_overdue", visit, now=now)
        elif visit.state in REMOTE | {"booking_pending"}:
            reason = None
            if not visit.review_due or visit.review_due <= now:
                reason = "Clinical review is due"
            elif not visit.approved_until or visit.approved_until <= now:
                reason = "Clinical attendance limit reached"
            elif service.paused or now - service.feed_at > settings.feed_ttl_seconds:
                reason = "Capacity feed unavailable; follow up with patient"
            else:
                slot = session.get(Slot, visit.slot_id) if visit.slot_id else None
                if slot and slot.end <= now:
                    reason = "Arrival window passed; confirm patient welfare"
            if reason:
                escalate(session, vault, service, visit, "watchdog", reason, now)
