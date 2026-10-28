import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import PREFIX, SERVICE, assessment, client_for, patient_for
from sqlalchemy import select

from virtualqueue.domain import public_visit, sweep_service, verify_audit
from virtualqueue.models import Audit, Job, Service, Slot, Visit
from virtualqueue.worker import tick


def test_patient_to_clinician_booking_then_care(env):
    app, db, vault, settings = env
    patient, visit_id = patient_for(app)
    assert patient.get("/api/patient/visit").json()["arrival_window"] is None
    r = assessment(env, visit_id)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "booking_pending"
    assert patient.get("/api/patient/visit").json()["arrival_window"] is None
    tick(db, vault, settings)
    current = patient.get("/api/patient/visit").json()
    assert current["state"] == "remote_waiting"
    assert current["arrival_window"] and current["approximately_ahead"] == 0
    assert "identity" not in current and "clinical" not in current
    staff = client_for(app, "clinician")
    for action, state in [("arrived", "arrived"), ("start_care", "in_care"), ("complete", "completed")]:
        r = staff.post(PREFIX + f"/visits/{visit_id}/action", json={"action": action,
                       "version": current["version"], "reason": "Synthetic care workflow validation"})
        assert r.status_code == 200, r.text
        current = r.json()
        assert current["state"] == state
    metrics = staff.get(PREFIX + "/metrics").json()
    assert metrics["treated"] == 1
    assert metrics["mean_total_minutes_to_care"] >= metrics["mean_minutes_on_site_before_care"]
    with db.read() as s:
        assert verify_audit(s, vault, s.get(Service, SERVICE))
        assert s.scalar(select(Slot).where(Slot.reserved > 0)) is None


@pytest.mark.parametrize("role", ["admin", "coordinator", "auditor", "integration"])
def test_only_clinicians_make_decisions(env, role):
    app, _, _, _ = env
    _, visit_id = patient_for(app)
    staff = client_for(app, role)
    r = staff.post(PREFIX + f"/visits/{visit_id}/action", json={"version": 1,
                   "action": "start_assessment", "reason": "Attempt an unauthorised clinical decision"})
    assert r.status_code == 403


@pytest.mark.parametrize("priority", [1, 2])
def test_high_acuity_cannot_wait_remotely(env, priority):
    _, visit_id = patient_for(env[0])
    assert assessment(env, visit_id, priority=priority).status_code == 422


def test_attend_now_requires_no_booking(env):
    patient, visit_id = patient_for(env[0])
    r = assessment(env, visit_id, decision="attend_now", priority=1, slot_id=None)
    assert r.status_code == 200
    assert patient.get("/api/patient/visit").json()["state"] == "attend_now"
    with env[1].read() as s:
        assert s.scalar(select(Job).where(Job.kind == "booking")) is None


def test_stale_version_cannot_overwrite_record(env):
    _, visit_id = patient_for(env[0])
    r = assessment(env, visit_id, version=1)
    assert r.status_code == 409


def test_two_clinicians_cannot_reserve_last_slot(env):
    app, db, _, _ = env
    ids = [patient_for(app, phone=f"+44770090012{i}")[1] for i in range(2)]
    with db.transaction(SERVICE) as s:
        for slot in s.scalars(select(Slot)):
            slot.capacity = 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda v: assessment(env, v), ids))
    assert sorted(r.status_code for r in responses) == [200, 409]
    with db.read() as s:
        assert sum(s.scalars(select(Slot.reserved))) == 1


def test_failed_booking_never_approves_remote_waiting(env):
    class Reject:
        def send(self, job, payload):
            return {"status": "rejected"}
    patient, visit_id = patient_for(env[0])
    assert assessment(env, visit_id).status_code == 200
    tick(env[1], env[2], env[3], gateway=Reject())
    current = patient.get("/api/patient/visit").json()
    assert current["state"] == "reassessment_required"
    assert current["arrival_window"] is None
    with env[1].read() as s:
        assert sum(s.scalars(select(Slot.reserved))) == 0


def test_unknown_booking_no_automatic_retry_or_closure(env):
    class Timeout:
        count = 0
        def send(self, job, payload):
            if job.kind == "booking":
                self.count += 1
                raise TimeoutError()
            return {"status": "simulated"}
    gateway = Timeout()
    patient, visit_id = patient_for(env[0])
    assert assessment(env, visit_id).status_code == 200
    for _ in range(3):
        tick(env[1], env[2], env[3], gateway=gateway)
    assert gateway.count == 1
    v = patient.get("/api/patient/visit").json()
    assert v["contact_needed"] and v["arrival_window"] is None
    r = client_for(env[0], "clinician").post(PREFIX + f"/visits/{visit_id}/action",
        json={"version": v["version"], "action": "close_after_review", "reason": "Checked patient welfare"})
    assert r.status_code == 409


def test_notification_failure_does_not_create_infinite_messages(env):
    class Failed:
        def send(self, job, payload):
            raise TimeoutError()
    _, visit_id = patient_for(env[0])
    assert assessment(env, visit_id, decision="attend_now", slot_id=None).status_code == 200
    for _ in range(5):
        tick(env[1], env[2], env[3], gateway=Failed())
    with env[1].read() as s:
        assert len(list(s.scalars(select(Job)))) <= 4


def test_worsening_during_cancellation_cannot_close_patient(env):
    patient, visit_id = patient_for(env[0])
    assert assessment(env, visit_id).status_code == 200
    tick(env[1], env[2], env[3])
    v = patient.get("/api/patient/visit").json()
    r = client_for(env[0], "clinician").post(PREFIX + f"/visits/{visit_id}/action",
        json={"version": v["version"], "action": "close_after_review", "reason": "Welfare review completed"})
    assert r.json()["state"] == "closure_pending"
    assert patient.post("/api/patient/action", json={"action": "worse"}).status_code == 200
    tick(env[1], env[2], env[3])
    assert patient.get("/api/patient/visit").json()["state"] == "reassessment_required"


@pytest.mark.parametrize("trigger", ["stale_feed", "review_due", "missed_arrival"])
def test_watchdog_withdraws_estimates_without_discharge(env, trigger):
    app, db, vault, settings = env
    patient, visit_id = patient_for(app)
    assert assessment(env, visit_id).status_code == 200
    tick(db, vault, settings)
    with db.transaction(SERVICE) as s:
        service, visit = s.get(Service, SERVICE), s.get(Visit, visit_id)
        if trigger == "stale_feed":
            service.feed_at = time.time() - 500
        elif trigger == "review_due":
            visit.review_due = time.time() - 1
        else:
            s.get(Slot, visit.slot_id).end = time.time() - 1
        assert public_visit(s, vault, settings, service, visit)["arrival_window"] is None
        sweep_service(s, vault, settings, service)
    v = patient.get("/api/patient/visit").json()
    assert v["state"] == "reassessment_required" and v["contact_needed"]
    assert v["approximately_ahead"] is None


def test_overdue_callback_remains_active(env):
    _, visit_id = patient_for(env[0])
    with env[1].transaction(SERVICE) as s:
        visit = s.get(Visit, visit_id)
        visit.callback_due = time.time() - 5
        sweep_service(s, env[2], env[3], s.get(Service, SERVICE))
        assert visit.state == "callback_requested" and visit.contact_needed


def test_travel_change_cannot_extend_clinical_limit(env):
    patient, visit_id = patient_for(env[0])
    assert assessment(env, visit_id).status_code == 200
    tick(env[1], env[2], env[3])
    with env[1].read() as s:
        deadline = s.get(Visit, visit_id).approved_until
    r = patient.post("/api/patient/journey", json={"minutes": 180, "buffer_minutes": 20, "mode": "taxi"})
    assert r.json()["state"] == "reassessment_required"
    with env[1].read() as s:
        assert s.get(Visit, visit_id).approved_until == deadline


def test_audit_detects_tampering(env):
    patient_for(env[0])
    with env[1].transaction(SERVICE) as s:
        s.scalar(select(Audit).order_by(Audit.sequence)).actor = "changed-actor"
    with env[1].read() as s:
        assert not verify_audit(s, env[2], s.get(Service, SERVICE))
