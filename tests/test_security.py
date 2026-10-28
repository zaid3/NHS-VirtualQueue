import time
from dataclasses import replace

import pytest
from conftest import PATIENT, PREFIX, SERVICE, client_for, patient_for
from sqlalchemy import select

from virtualqueue.models import Challenge, Service, SessionRecord, Visit
from virtualqueue.security import issue_session


def test_authentication_csrf_origin_and_security_headers(env):
    client = client_for(env[0])
    assert client.get(PREFIX + "/visits").status_code == 401
    assert client.post("/api/dev/login", json={}, headers={"Origin": "https://attacker.invalid"}).status_code == 403
    client = client_for(env[0], "clinician")
    assert client.post(PREFIX + "/pause", json={"paused": True, "reason": "Test service shutdown"},
                       headers={"X-CSRF-Token": "invalid"}).status_code == 403
    r = client.get("/")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"
    assert client.get("/", headers={"Host": "evil.invalid"}).status_code == 400
    assert "httponly" in client.post("/api/dev/login", json={}).headers["Set-Cookie"].lower()


def test_cross_tenant_access_and_patient_isolation(env):
    app, db, vault, settings = env
    alice, a = patient_for(app)
    bob, b = patient_for(app, phone="+447700900124", name="Bob Synthetic")
    assert alice.get("/api/patient/visit").json()["id"] == a
    assert bob.get("/api/patient/visit").json()["id"] == b
    assert alice.get(PREFIX + "/visits").status_code == 403
    with db.transaction() as s:
        token, csrf = issue_session(s, vault, settings, "other-clinician", "clinician", "another-trust")
    other = client_for(app)
    other.cookies.set("vq_session", token)
    other.headers["X-CSRF-Token"] = csrf
    assert other.get(PREFIX + "/visits").status_code == 403
    assert other.get(PREFIX + "/jobs").status_code == 403


def test_encryption_at_rest_and_validation_redaction(env):
    _, v = patient_for(env[0])
    with env[1].read() as s:
        visit = s.get(Visit, v)
        assert PATIENT["name"] not in visit.identity
        assert PATIENT["phone"] not in visit.identity
        assert env[2].open(visit.identity)["birth_date"] == PATIENT["birth_date"]
        assert len(list(s.scalars(select(SessionRecord)))) == 1
    r = client_for(env[0]).post("/api/patient/start", json={**PATIENT, "phone": "private-invalid-value"})
    assert r.status_code == 422 and "private-invalid-value" not in r.text


def test_otp_locks_after_five_wrong_attempts(env):
    client = client_for(env[0])
    challenge = client.post("/api/patient/start", json=PATIENT).json()
    wrong = "000000" if challenge["test_code"] != "000000" else "000001"
    for _ in range(5):
        assert client.post("/api/patient/verify", json={"challenge_id": challenge["challenge_id"], "code": wrong}).status_code == 400
    assert client.post("/api/patient/verify", json={"challenge_id": challenge["challenge_id"], "code": challenge["test_code"]}).status_code == 400
    with env[1].read() as s:
        assert s.get(Challenge, challenge["challenge_id"]).attempts == 5


@pytest.mark.parametrize("expired", [False, True])
def test_otp_replay_and_expiry(env, expired):
    c = client_for(env[0])
    ch = c.post("/api/patient/start", json=PATIENT).json()
    payload = {"challenge_id": ch["challenge_id"], "code": ch["test_code"]}
    if expired:
        with env[1].transaction(SERVICE) as s:
            s.get(Challenge, ch["challenge_id"]).expires = time.time() - 1
    else:
        assert c.post("/api/patient/verify", json=payload).status_code == 200
    assert c.post("/api/patient/verify", json=payload).status_code == 400


def test_otp_rate_limit(env):
    c = client_for(env[0])
    for _ in range(5):
        assert c.post("/api/patient/start", json=PATIENT).status_code == 200
    assert c.post("/api/patient/start", json=PATIENT).status_code == 429


def test_resume_needs_reference_and_same_verified_phone(env):
    _, ref = patient_for(env[0])
    c = client_for(env[0])
    ch = c.post("/api/patient/start", json={**PATIENT, "phone": "+447700900125", "existing_reference": ref}).json()
    assert c.post("/api/patient/verify", json={"challenge_id": ch["challenge_id"], "code": ch["test_code"]}).status_code == 400


def test_emergency_and_unavailable_service_do_not_create_request(env):
    c = client_for(env[0])
    assert c.post("/api/patient/start", json={**PATIENT, "emergency": True}).status_code == 422
    with env[1].transaction(SERVICE) as s:
        s.get(Service, SERVICE).paused = True
    assert c.post("/api/patient/start", json=PATIENT).status_code == 409


def test_request_size_limit(env):
    c = client_for(env[0])
    assert c.post("/api/patient/start", content="x" * 70000).status_code == 413


def test_production_configuration_fails_closed(env):
    with pytest.raises(ValueError, match="Production requires"):
        replace(env[3], mode="production").validate()
    configured = replace(env[3], mode="production", public_url="https://queue.example.org",
        database_url="postgresql+psycopg://example", oidc_issuer="https://id.example.org",
        oidc_client_id="client", oidc_client_secret="secret", gateway_url="https://gateway.example.org",
        gateway_key="x" * 40, gateway_cert="cert.pem", gateway_private_key="key.pem", clinical_release_id="TEST-ONLY")
    assert configured.validate() is configured
    with pytest.raises(ValueError, match="HTTPS"):
        replace(configured, public_url="http://queue.example.org").validate()
    from virtualqueue.app import create_app
    c = client_for(create_app(configured, env[1]))
    c.base_url = "https://queue.example.org"
    c.headers["Origin"] = "https://queue.example.org"
    assert c.post("/api/dev/login", json={}).status_code == 404


def test_demographic_link_requires_integration_role_and_exact_fields(env):
    patient, ref = patient_for(env[0])
    path = f"/api/integration/services/{SERVICE}/visits/{ref}/demographics"
    data = {"version": 1, "birth_date": PATIENT["birth_date"], "postcode": PATIENT["postcode"], "family_name": "Example",
            "status": "matched", "nhs_number": "9434765919", "source_reference": "SYNTHETIC-PDS-1"}
    assert patient.post(path, json=data).status_code == 403
    integration = client_for(env[0], "integration")
    assert integration.post(path, json={**data, "postcode": "SW1A 1AA"}).status_code == 409
    assert integration.post(path, json={**data, "family_name": "Different"}).status_code == 409
    assert integration.post(path, json={**data, "nhs_number": "1234567890"}).status_code == 422
    assert integration.post(path, json=data).status_code == 200
    assert "9434765919" not in patient.get("/api/patient/visit").text


def test_ambulance_source_does_not_grant_priority(env):
    c = client_for(env[0], "coordinator")
    data = {k: PATIENT[k] for k in ["name", "family_name", "birth_date", "postcode", "phone", "concern"]}
    r = c.post(PREFIX + "/requests", json={**data, "source": "ambulance", "external_reference": "SYNTHETIC-999"})
    assert r.status_code == 200, r.text
    v = c.get(PREFIX + "/visits").json()[0]
    assert v["priority"] == 5 and v["state"] == "callback_requested"
