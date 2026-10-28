import time
from datetime import UTC, datetime, timedelta

from conftest import PREFIX, SERVICE, assessment, client_for, patient_for
from sqlalchemy import select

from virtualqueue.domain import accept_booking
from virtualqueue.models import Job, Service, Slot, Visit
from virtualqueue.worker import tick


def test_capacity_feed_version_and_reserved_slot_integrity(env):
    app, db, _, _ = env
    _, visit_id = patient_for(app)
    assert assessment(env, visit_id).status_code == 200
    with db.read() as s:
        service = s.get(Service, SERVICE)
        slot = s.get(Slot, s.get(Visit, visit_id).slot_id)
        payload = {"version": service.feed_version, "observed_at": datetime.now(UTC).isoformat(),
            "slots": [{"external_id": slot.external_id, "start": datetime.fromtimestamp(slot.start, UTC).isoformat(),
                       "end": datetime.fromtimestamp(slot.end, UTC).isoformat(), "capacity": 4}]}
    integration = client_for(app, "integration")
    path = f"/api/integration/services/{SERVICE}/capacity"
    assert integration.post(path, json=payload).status_code == 409
    payload["version"] += 1
    payload["slots"][0]["start"] = (datetime.now(UTC) + timedelta(minutes=75)).isoformat()
    assert integration.post(path, json=payload).status_code == 409
    payload["slots"] = []
    assert integration.post(path, json=payload).status_code == 200
    assert client_for(app, "clinician").get(PREFIX + "/visits").json()[0]["state"] == "reassessment_required"


def test_late_booking_preserves_in_person_care_state(env):
    app, db, vault, _ = env
    _, visit_id = patient_for(app)
    assert assessment(env, visit_id).status_code == 200
    with db.transaction(SERVICE) as s:
        visit = s.get(Visit, visit_id)
        visit.state = "in_care"
        job = s.scalar(select(Job).where(Job.kind == "booking"))
        accept_booking(s, vault, s.get(Service, SERVICE), visit, job, "SYNTHETIC-LATE")
        assert visit.state == "in_care" and visit.contact_needed


def test_booking_change_before_dispatch_releases_unsent_reservation(env):
    patient, visit_id = patient_for(env[0])
    assert assessment(env, visit_id).status_code == 200
    assert patient.post("/api/patient/journey", json={"minutes": 25, "mode": "taxi"}).status_code == 200
    tick(env[1], env[2], env[3])
    assert patient.get("/api/patient/visit").json()["state"] == "reassessment_required"
    with env[1].read() as s:
        assert sum(s.scalars(select(Slot.reserved))) == 0


def test_worker_crash_keeps_unknown_booking_for_reconciliation(env):
    _, visit_id = patient_for(env[0])
    assert assessment(env, visit_id).status_code == 200
    with env[1].transaction(SERVICE) as s:
        job = s.scalar(select(Job).where(Job.kind == "booking"))
        job.state, job.claimed_at, job.attempts = "sending", time.time() - 90, 1
        job_id = job.id
    tick(env[1], env[2], env[3])
    with env[1].read() as s:
        assert s.get(Job, job_id).state == "unknown" and s.get(Job, job_id).attempts == 1
    integration = client_for(env[0], "integration")
    path = f"/api/integration/services/{SERVICE}/jobs/{job_id}/reconcile"
    data = {"accepted": True, "external_reference": "SYNTHETIC-FOUND", "reason": "Receiving service confirmed its booking record"}
    assert integration.post(path, json=data).status_code == 200
    assert integration.post(path, json=data).status_code == 409
    with env[1].read() as s:
        assert s.get(Visit, visit_id).state == "reassessment_required"


def test_booking_cancellation_waits_for_external_acknowledgement(env):
    patient, visit_id = patient_for(env[0])
    assert assessment(env, visit_id).status_code == 200
    tick(env[1], env[2], env[3])
    v = patient.get("/api/patient/visit").json()
    r = client_for(env[0], "clinician").post(PREFIX + f"/visits/{visit_id}/action",
        json={"action": "cancel_booking", "version": v["version"], "reason": "Patient needs a revised clinical plan"})
    assert r.json()["state"] == "cancellation_pending" and r.json()["slot_id"]
    tick(env[1], env[2], env[3])
    with env[1].read() as s:
        visit = s.get(Visit, visit_id)
        assert visit.slot_id is None and visit.booking_reference is None and visit.contact_needed


def test_accepted_message_without_receipt_needs_followup(env):
    class Accepted:
        def send(self, job, payload):
            return {"status": "accepted", "reference": "SYNTHETIC-PROVIDER"}
    patient, visit_id = patient_for(env[0])
    assert assessment(env, visit_id, decision="attend_now", slot_id=None).status_code == 200
    tick(env[1], env[2], env[3], gateway=Accepted())
    with env[1].transaction(SERVICE) as s:
        job = s.scalar(select(Job).where(Job.kind == "notification"))
        job.claimed_at = time.time() - 190
        job_id = job.id
    tick(env[1], env[2], env[3], gateway=Accepted())
    assert patient.get("/api/patient/visit").json()["contact_needed"]
    r = client_for(env[0], "integration").post(f"/api/integration/services/{SERVICE}/jobs/{job_id}/receipt",
           json={"delivered": True, "provider_reference": "SYNTHETIC-RECEIPT"})
    assert r.json()["state"] == "delivered"
    assert patient.get("/api/patient/visit").json()["contact_needed"]
