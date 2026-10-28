import os
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from virtualqueue.app import create_app
from virtualqueue.cli import seed
from virtualqueue.config import Settings
from virtualqueue.db import Database
from virtualqueue.models import Base, Slot
from virtualqueue.security import Vault

SERVICE = "riverside-urgent-care"
PREFIX = "/api/staff/services/" + SERVICE
PATIENT = {"service_id": SERVICE, "name": "Alex Example", "family_name": "Example",
           "birth_date": "1990-06-02", "postcode": "E6 1AA", "phone": "+447700900123",
           "concern": "Synthetic test request", "emergency": False, "consent_to_contact": True}


@pytest.fixture
def env(tmp_path, monkeypatch):
    url = os.getenv("TEST_DATABASE_URL", "sqlite:///" + str(tmp_path / "test.db"))
    settings = Settings(mode="test", database_url=url, encryption_key=Fernet.generate_key().decode(),
                        signing_key=secrets.token_urlsafe(48), public_url="http://127.0.0.1:8000",
                        gateway_url="", oidc_issuer="", google_routes_key="")
    db = Database(url)
    # TEST_DATABASE_URL must identify a disposable database, never a care database.
    Base.metadata.drop_all(db.engine)
    with db.engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    vault = Vault(settings)
    seed(db, vault, settings)
    app = create_app(settings, db)
    yield app, db, vault, settings
    db.engine.dispose()


def client_for(app, role=None):
    client = TestClient(app, base_url="http://127.0.0.1:8000", headers={"Origin": "http://127.0.0.1:8000"})
    if role:
        r = client.post("/api/dev/login", json={"role": role})
        assert r.status_code == 200, r.text
        client.headers["X-CSRF-Token"] = r.json()["csrf"]
    return client


def patient_for(app, **overrides):
    client = client_for(app)
    r = client.post("/api/patient/start", json={**PATIENT, **overrides})
    assert r.status_code == 200, r.text
    challenge = r.json()
    r = client.post("/api/patient/verify", json={"challenge_id": challenge["challenge_id"], "code": challenge["test_code"]})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf"]
    return client, r.json()["reference"]


def assessment(env, visit_id, **overrides):
    app, db, _, _ = env
    clinician = client_for(app, "clinician")
    visits = clinician.get(PREFIX + "/visits").json()
    v = next(v for v in visits if v["id"] == visit_id)
    if v["state"] == "callback_requested":
        r = clinician.post(PREFIX + f"/visits/{visit_id}/action", json={"version": v["version"],
                           "action": "start_assessment", "reason": "Telephone assessment started"})
        assert r.status_code == 200, r.text
        v = r.json()
    with db.read() as s:
        slot = s.scalar(select(Slot).where(Slot.service_id == SERVICE).order_by(Slot.start))
        slot_id = slot.id
    now = datetime.now(UTC)
    data = {"version": v["version"], "decision": "remote_wait", "priority": 4,
            "assessment_reference": "SYNTHETIC-EPR-1", "identity_confirmed": True,
            "phone_assessment_completed": True, "notes": "Synthetic clinician assessment record only.",
            "safety_net": "Follow the agreed clinical plan. For an emergency call 999.",
            "review_due": (now + timedelta(minutes=100)).isoformat(),
            "approved_until": (now + timedelta(hours=3)).isoformat(), "slot_id": slot_id, **overrides}
    return clinician.post(PREFIX + f"/visits/{visit_id}/assessment", json=data)
