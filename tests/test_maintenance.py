import time

from conftest import PATIENT, SERVICE, client_for, patient_for
from sqlalchemy import select

from virtualqueue.domain import escalate
from virtualqueue.models import Challenge, Job, Service, Visit
from virtualqueue.worker import tick


def test_worsening_in_person_preserves_recorded_care_location(env):
    _, ref = patient_for(env[0])
    with env[1].transaction(SERVICE) as s:
        visit = s.get(Visit, ref)
        visit.state = "in_care"
        escalate(s, env[2], s.get(Service, SERVICE), visit, "patient", "Worsening symptoms")
        assert visit.state == "in_care" and visit.contact_needed


def test_expired_otp_is_purged_and_delivery_payload_redacted(env):
    c = client_for(env[0])
    ch = c.post("/api/patient/start", json=PATIENT).json()
    with env[1].transaction(SERVICE) as s:
        s.get(Challenge, ch["challenge_id"]).expires = time.time() - 1
        job = s.scalar(select(Job).where(Job.kind == "verification_code"))
        job.created = time.time() - 400
        identifier = job.id
    tick(env[1], env[2], env[3])
    with env[1].read() as s:
        assert s.get(Challenge, ch["challenge_id"]) is None
        assert env[2].open(s.get(Job, identifier).payload) == {"redacted": True}
        assert s.get(Job, identifier).state == "cancelled"
