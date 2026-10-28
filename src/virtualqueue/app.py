import hmac
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from sqlalchemy import select, text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .adapters import TravelEstimator
from .auth import IdentityProvider
from .config import Settings
from .db import Database
from .demographics import postcode_key, valid_nhs_number
from .domain import (
    OPERATORS,
    REMOTE,
    TERMINAL,
    accept_booking,
    apply_feed,
    assess,
    audit,
    check_version,
    create_visit,
    enqueue,
    escalate,
    finish_cancellation,
    notice,
    public_visit,
    release_slot,
    service_for,
    staff_visit,
    touch,
    transition,
    verify_audit,
    visit_for,
)
from .models import Audit, AuthFlow, Challenge, Job, Service, SessionRecord, Slot, Visit, uid
from .schemas import (
    Assessment,
    AssistedRequest,
    DemographicMatch,
    Feed,
    Input,
    Journey,
    PatientStart,
    Reconcile,
    RouteRequest,
    ServiceInput,
    Transition,
    VerifyCode,
)
from .security import Actor, Vault, issue_session, rate_limit, set_session_cookie

STATIC = Path(__file__).parent / "static"


class SecurityMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        method = scope["method"]
        if method not in {"GET", "HEAD", "OPTIONS"} and not headers.get(b"authorization", b"").startswith(b"Bearer "):
            if headers.get(b"origin", b"").decode() != self.settings.public_url:
                return await JSONResponse({"detail": "Request origin rejected"}, 403)(scope, receive, send)
        length = headers.get(b"content-length", b"0")
        if not length.isdigit() or int(length) > self.settings.max_body_bytes:
            return await JSONResponse({"detail": "Request too large"}, 413)(scope, receive, send)
        consumed, started = 0, False

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > self.settings.max_body_bytes:
                raise ValueError("body_limit_exceeded")
            return message

        async def secure_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                extra = [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                         (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY"),
                         (b"permissions-policy", b"camera=(), microphone=(), geolocation=(self)"),
                         (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
                if self.settings.mode == "production":
                    extra.append((b"strict-transport-security", b"max-age=31536000"))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        try:
            await self.app(scope, limited_receive, secure_send)
        except ValueError as exc:
            if str(exc) == "body_limit_exceeded" and not started:
                return await JSONResponse({"detail": "Request too large"}, 413)(scope, receive, secure_send)
            raise


def create_app(settings=None, db=None):
    settings = (settings or Settings()).validate()
    db, vault = db or Database(settings.database_url), Vault(settings)
    app = FastAPI(title="NHS VirtualQueue", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url="/openapi.json" if settings.mode != "production" else None)
    app.state.db, app.state.vault, app.state.settings = db, vault, settings
    app.state.identity = IdentityProvider(settings)
    app.state.travel = TravelEstimator(settings.google_routes_key)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlparse(settings.public_url).hostname,
                                                            "localhost", "127.0.0.1", "testserver"]
                       if settings.mode != "production" else [urlparse(settings.public_url).hostname])
    app.add_middleware(SecurityMiddleware, settings=settings)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
                                        for e in exc.errors()]}, 422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse({"detail": "The service could not complete this request. Contact your care team if needed."}, 500)

    def actor(request: Request):
        bearer = request.headers.get("authorization", "")
        if bearer.startswith("Bearer "):
            try:
                return app.state.identity.access_actor(bearer[7:])
            except Exception:
                raise HTTPException(401, "Invalid access token") from None
        token = request.cookies.get("vq_session", "")
        with db.read() as session:
            record = session.get(SessionRecord, vault.digest(token))
            if not record or record.expires <= time.time():
                raise HTTPException(401, "Please sign in again")
            if request.method not in {"GET", "HEAD"} and not hmac.compare_digest(
                request.headers.get("x-csrf-token", ""), record.csrf
            ):
                raise HTTPException(403, "Request verification failed. Refresh and try again.")
            return Actor(record.subject, record.role, record.tenant, record.visit_id, record.csrf)

    def patient_service(who):
        if who.role != "patient":
            raise HTTPException(403, "Patient sign-in required")
        with db.read() as session:
            visit = visit_for(session, who.visit_id)
            return visit.service_id

    @app.get("/health/live")
    def live():
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready")
    def ready():
        try:
            with db.read() as session:
                revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                if revision != "0001":
                    raise ValueError("schema revision")
            return {"status": "ok", "clinical_release": bool(settings.clinical_release_id)}
        except Exception:
            return JSONResponse({"status": "unavailable"}, 503)

    @app.get("/api/config")
    def config():
        with db.read() as session:
            services = [{"id": s.id, "name": s.name, "address": s.address, "phone": s.phone,
                         "accepting": not s.paused and time.time() - s.feed_at <= settings.feed_ttl_seconds}
                        for s in session.scalars(select(Service).order_by(Service.name))]
        return {"mode": settings.mode, "version": __version__, "services": services,
                "live_travel_configured": bool(settings.google_routes_key),
                "independent_project": True, "staff_signin_configured": bool(settings.oidc_issuer)}

    @app.get("/api/me")
    def me(who: Actor = Depends(actor)):
        return {"role": who.role, "tenant": who.tenant, "csrf": who.csrf, "visit_id": who.visit_id}

    @app.post("/api/logout")
    def logout(request: Request, who: Actor = Depends(actor)):
        with db.transaction() as session:
            record = session.get(SessionRecord, vault.digest(request.cookies.get("vq_session", "")))
            if record:
                session.delete(record)
        response = JSONResponse({"ok": True})
        response.delete_cookie("vq_session")
        return response

    @app.get("/auth/start")
    def auth_start():
        try:
            with db.transaction() as session:
                url, state = app.state.identity.begin(session, vault)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "Staff sign-in is temporarily unavailable") from None
        response = RedirectResponse(url, 302)
        response.set_cookie("vq_oidc", state, httponly=True, secure=settings.mode == "production",
                            samesite="lax", max_age=300)
        return response

    @app.get("/auth/callback")
    def auth_callback(request: Request, state: str = "", code: str = ""):
        if not state or not code or not hmac.compare_digest(state, request.cookies.get("vq_oidc", "")):
            raise HTTPException(401, "Sign-in could not be verified")
        with db.transaction() as session:
            flow = session.get(AuthFlow, vault.digest(state))
            if not flow or flow.expires <= time.time():
                raise HTTPException(401, "Sign-in expired")
            verifier, nonce = vault.open(flow.verifier), flow.nonce
            session.delete(flow)
        try:
            claims = app.state.identity.exchange(code, verifier, nonce)
        except Exception:
            raise HTTPException(401, "Staff identity or MFA could not be verified") from None
        with db.transaction() as session:
            token, _ = issue_session(session, vault, settings, claims["sub"], claims["vq_role"],
                                     claims["vq_tenant"], expires=claims["exp"])
        response = RedirectResponse("/staff", 302)
        set_session_cookie(response, token, settings)
        response.delete_cookie("vq_oidc")
        return response

    class DevLogin(Input):
        role: str = "clinician"

    @app.post("/api/dev/login")
    def dev_login(data: DevLogin, request: Request):
        if settings.mode == "production" or (settings.mode != "test" and request.client.host not in {"127.0.0.1", "::1"}):
            raise HTTPException(404, "Not found")
        if data.role not in {"clinician", "coordinator", "admin", "auditor", "integration"}:
            raise HTTPException(422, "Unknown role")
        with db.transaction() as session:
            token, csrf = issue_session(session, vault, settings, "local-" + data.role, data.role, "synthetic-trust")
        response = JSONResponse({"csrf": csrf, "role": data.role})
        set_session_cookie(response, token, settings)
        return response

    @app.post("/api/patient/start")
    def patient_start(data: PatientStart, request: Request):
        if data.emergency:
            raise HTTPException(422, "For a life-threatening emergency call 999 now. Do not wait for this service.")
        now, challenge_id = time.time(), uid()
        with db.transaction(data.service_id) as session:
            service = service_for(session, data.service_id)
            rate_limit(session, vault, "otp-phone:" + data.phone, 5, 3600)
            rate_limit(session, vault, "otp-ip:" + request.client.host, 20, 3600)
            if service.paused or now - service.feed_at > settings.feed_ttl_seconds:
                raise HTTPException(409, "Online requests are unavailable. Contact NHS 111 or the care team.")
            code = f"{secrets.randbelow(1000000):06d}"
            session.add(Challenge(id=challenge_id, service_id=service.id, payload=vault.seal(data.model_dump(mode="json")),
                                  code_hash=vault.digest(challenge_id + code), expires=now + 300))
            enqueue(session, vault, service, "verification_code", {"phone": data.phone, "code": code,
                    "expires": now + 300}, dedupe=challenge_id)
        result = {"challenge_id": challenge_id, "expires_in": 300}
        if settings.mode != "production":
            result["test_code"] = code
        return result

    @app.post("/api/patient/verify")
    def verify_code(data: VerifyCode):
        with db.read() as session:
            c = session.get(Challenge, data.challenge_id)
            if not c:
                raise HTTPException(400, "Code is invalid or expired")
            service_id = c.service_id
        error, token, result = None, None, None
        with db.transaction(service_id) as session:
            challenge = session.get(Challenge, data.challenge_id)
            if challenge.used or challenge.attempts >= 5 or challenge.expires <= time.time():
                error = "Code is invalid or expired"
            else:
                challenge.attempts += 1
                if not hmac.compare_digest(challenge.code_hash, vault.digest(challenge.id + data.code)):
                    error = "Code is invalid or expired"
                else:
                    challenge.used = True
                    payload, service = vault.open(challenge.payload), service_for(session, service_id)
                    if payload.get("existing_reference"):
                        visit = session.get(Visit, payload["existing_reference"])
                        if (not visit or visit.service_id != service_id
                                or visit.phone_hash != vault.digest(payload["phone"])):
                            error = "Reference could not be verified"
                    else:
                        visit = create_visit(session, vault, service, payload)
                    if not error:
                        token, csrf = issue_session(session, vault, settings, "patient:" + visit.id,
                                                     "patient", service.tenant, visit.id)
                        audit(session, vault, service, "patient:" + visit.id, "patient_session_opened", visit)
                        result = {"reference": visit.id, "csrf": csrf}
        if error:
            raise HTTPException(400, error)
        response = JSONResponse(result)
        set_session_cookie(response, token, settings)
        return response

    @app.get("/api/patient/visit")
    def patient_status(who: Actor = Depends(actor)):
        service_id = patient_service(who)
        with db.read() as session:
            return public_visit(session, vault, settings, service_for(session, service_id), visit_for(session, who.visit_id))

    @app.post("/api/patient/journey")
    def patient_journey(data: Journey, who: Actor = Depends(actor)):
        service_id = patient_service(who)
        with db.transaction(service_id) as session:
            service, visit = service_for(session, service_id), visit_for(session, who.visit_id)
            if visit.state in TERMINAL | {"arrived", "in_care"}:
                raise HTTPException(409, "Journey cannot be changed at this stage")
            if data.mode == "ambulance":
                raise HTTPException(422, "Ambulance transport must be arranged through the clinical service")
            visit.journey_minutes, visit.buffer_minutes, visit.travel_mode = data.minutes, data.buffer_minutes, data.mode
            visit.travel_source, visit.travel_updated = "patient_estimate", time.time()
            touch(visit, time.time())
            audit(session, vault, service, who.subject, "journey_updated", visit)
            if visit.state in REMOTE:
                slot = session.get(Slot, visit.slot_id)
                if not slot or time.time() + (data.minutes + data.buffer_minutes) * 60 > slot.end:
                    escalate(session, vault, service, visit, who.subject, "Journey no longer fits the approved window")
                else:
                    notice(session, vault, service, visit, "departure_reminder", due=slot.start - (data.minutes + data.buffer_minutes) * 60)
            return public_visit(session, vault, settings, service, visit)

    @app.post("/api/patient/route")
    def patient_route(data: RouteRequest, who: Actor = Depends(actor)):
        service_id = patient_service(who)
        with db.transaction(service_id) as session:
            rate_limit(session, vault, "route:" + who.visit_id, 12, 3600)
            service = service_for(session, service_id)
            destination = {"latitude": service.latitude, "longitude": service.longitude}
        try:
            return app.state.travel.estimate({"latitude": data.latitude, "longitude": data.longitude}, destination, data.mode)
        except (ValueError, KeyError, IndexError, httpx.HTTPError):
            raise HTTPException(503, "Journey estimate unavailable. Enter your own estimate or contact the care team.") from None

    class PatientAction(Input):
        action: str = Field(pattern=r"^(worse|travelling|cancel_request)$")

    @app.post("/api/patient/action")
    def patient_action(data: PatientAction, who: Actor = Depends(actor)):
        service_id = patient_service(who)
        with db.transaction(service_id) as session:
            service, visit = service_for(session, service_id), visit_for(session, who.visit_id)
            if data.action == "travelling":
                if (visit.state not in {"remote_waiting", "called_in"} or not visit.review_due
                        or visit.review_due <= time.time() or service.paused
                        or time.time() - service.feed_at > settings.feed_ttl_seconds):
                    raise HTTPException(409, "Contact your care team and follow your last clinical instructions")
                visit.state = "travelling"
                touch(visit, time.time())
                audit(session, vault, service, who.subject, "travelling", visit)
            else:
                reason = "Patient reports worsening symptoms" if data.action == "worse" else "Patient requests cancellation; welfare review required"
                escalate(session, vault, service, visit, who.subject, reason)
            return public_visit(session, vault, settings, service, visit)

    @app.get("/api/staff/services")
    def staff_services(who: Actor = Depends(actor)):
        if who.role not in OPERATORS | {"integration", "auditor"}:
            raise HTTPException(403, "Staff sign-in required")
        with db.read() as session:
            return [{"id": s.id, "name": s.name, "paused": s.paused, "feed_at": s.feed_at,
                     "feed_version": s.feed_version, "phone": s.phone}
                    for s in session.scalars(select(Service).where(Service.tenant == who.tenant))]

    @app.post("/api/staff/services")
    def add_service(data: ServiceInput, who: Actor = Depends(actor)):
        if who.role != "admin":
            raise HTTPException(403, "Administrator role required")
        with db.transaction() as session:
            if session.get(Service, data.id):
                raise HTTPException(409, "Service identifier already exists")
            service = Service(**data.model_dump(), tenant=who.tenant, feed_at=0, feed_version=0,
                              audit_seq=0, audit_head="0" * 64, paused=True)
            session.add(service)
            audit(session, vault, service, who.subject, "service_created")
        return {"id": data.id}

    @app.get("/api/staff/services/{service_id}/visits")
    def staff_visits(service_id: str, who: Actor = Depends(actor), offset: int = Query(default=0, ge=0),
                     limit: int = Query(default=200, ge=1, le=200)):
        with db.transaction(service_id) as session:
            service = service_for(session, service_id)
            who.require(service, OPERATORS)
            visits = list(session.scalars(select(Visit).where(Visit.service_id == service_id,
                                          Visit.state.not_in(TERMINAL)).order_by(Visit.priority, Visit.created, Visit.id).offset(offset).limit(limit)))
            audit(session, vault, service, who.subject, "staff_list_viewed", details={"records": len(visits)})
            return [staff_visit(session, vault, settings, service, v, include_clinical=who.role == "clinician") for v in visits]

    @app.post("/api/staff/services/{service_id}/requests")
    def assisted_request(service_id: str, data: AssistedRequest, who: Actor = Depends(actor)):
        with db.transaction(service_id) as session:
            service = service_for(session, service_id)
            who.require(service, OPERATORS | {"integration"})
            visit = create_visit(session, vault, service, data.model_dump(mode="json"), data.source, who.subject)
            if data.requires_immediate_assessment:
                visit.state, visit.contact_needed = "attend_now", True
                audit(session, vault, service, who.subject, "urgent_inbound_alert", visit,
                      {"source_reference": data.external_reference})
            return {"id": visit.id, "version": visit.version}

    @app.post("/api/integration/services/{service_id}/visits/{visit_id}/demographics")
    def record_demographic_match(service_id: str, visit_id: str, data: DemographicMatch, who: Actor = Depends(actor)):
        with db.transaction(service_id) as session:
            service, visit = service_for(session, service_id), visit_for(session, visit_id, service_id)
            who.require(service, {"integration"})
            check_version(visit, data.version)
            identity = vault.open(visit.identity)
            if (identity.get("birth_date") != str(data.birth_date)
                    or postcode_key(identity.get("postcode", "")) != postcode_key(data.postcode)
                    or identity.get("family_name", "").casefold() != data.family_name.casefold()):
                raise HTTPException(409, "Demographic mismatch requires manual review; no record was linked")
            if data.status == "matched" and not (valid_nhs_number(data.nhs_number) or data.local_record_reference):
                raise HTTPException(422, "A valid NHS number or receiving-service record reference is required")
            identity["record_match"] = data.model_dump(mode="json")
            visit.identity = vault.seal(identity)
            touch(visit, time.time())
            audit(session, vault, service, who.subject, "demographics_" + data.status, visit,
                  {"source_reference": data.source_reference})
        return {"status": data.status, "version": visit.version}

    @app.get("/api/staff/services/{service_id}/slots")
    def slots(service_id: str, who: Actor = Depends(actor)):
        with db.read() as session:
            service = service_for(session, service_id)
            who.require(service, OPERATORS | {"integration"})
            return [{"id": s.id, "start": s.start, "end": s.end, "available": max(0, s.capacity - s.reserved)}
                    for s in session.scalars(select(Slot).where(Slot.service_id == service_id,
                                             Slot.enabled.is_(True), Slot.end > time.time()).order_by(Slot.start))]

    @app.post("/api/integration/services/{service_id}/capacity")
    def capacity(service_id: str, data: Feed, who: Actor = Depends(actor)):
        with db.transaction(service_id) as session:
            service = service_for(session, service_id)
            apply_feed(session, vault, service, who, data)
        return {"accepted_version": data.version}

    @app.post("/api/staff/services/{service_id}/visits/{visit_id}/assessment")
    def record_assessment(service_id: str, visit_id: str, data: Assessment, who: Actor = Depends(actor)):
        with db.transaction(service_id) as session:
            service, visit = service_for(session, service_id), visit_for(session, visit_id, service_id)
            assess(session, vault, settings, service, visit, who, data)
            return staff_visit(session, vault, settings, service, visit)

    @app.post("/api/staff/services/{service_id}/visits/{visit_id}/action")
    def staff_action(service_id: str, visit_id: str, data: Transition, who: Actor = Depends(actor)):
        with db.transaction(service_id) as session:
            service, visit = service_for(session, service_id), visit_for(session, visit_id, service_id)
            transition(session, vault, service, visit, who, data)
            return staff_visit(session, vault, settings, service, visit, include_clinical=who.role == "clinician")

    class Pause(Input):
        paused: bool
        reason: str = Field(min_length=10, max_length=500)

    @app.post("/api/staff/services/{service_id}/pause")
    def pause(service_id: str, data: Pause, who: Actor = Depends(actor)):
        with db.transaction(service_id) as session:
            service = service_for(session, service_id)
            who.require(service, {"admin", "clinician"})
            service.paused = data.paused
            audit(session, vault, service, who.subject, "service_paused" if data.paused else "service_resumed",
                  details={"reason": data.reason})
            if data.paused:
                for visit in session.scalars(select(Visit).where(Visit.service_id == service_id, Visit.state.in_(REMOTE | {"booking_pending"}))):
                    escalate(session, vault, service, visit, who.subject, "Remote waiting paused by care team")
        return {"paused": data.paused}

    @app.get("/api/staff/services/{service_id}/jobs")
    def jobs(service_id: str, who: Actor = Depends(actor)):
        with db.read() as session:
            who.require(service_for(session, service_id), OPERATORS | {"integration", "auditor"})
            return [{"id": j.id, "visit_id": j.visit_id, "kind": j.kind, "state": j.state,
                     "attempts": j.attempts, "created": j.created, "error": j.error}
                    for j in session.scalars(select(Job).where(Job.service_id == service_id).order_by(Job.created.desc()).limit(100))]

    @app.post("/api/integration/services/{service_id}/jobs/{job_id}/reconcile")
    def reconcile(service_id: str, job_id: str, data: Reconcile, who: Actor = Depends(actor)):
        with db.transaction(service_id) as session:
            service = service_for(session, service_id)
            who.require(service, {"integration"})
            job = session.get(Job, job_id)
            if not job or job.service_id != service_id:
                raise HTTPException(404, "Delivery not found")
            if job.state != "unknown":
                raise HTTPException(409, "Only an unknown outcome can be reconciled")
            visit = session.get(Visit, job.visit_id) if job.visit_id else None
            if job.kind == "booking" and data.accepted:
                accept_booking(session, vault, service, visit, job, data.external_reference)
            elif job.kind == "booking":
                job.state = "rejected"
                release_slot(session, visit)
            elif job.kind == "cancel_booking" and data.accepted:
                finish_cancellation(session, vault, service, visit, job)
            else:
                job.state = "accepted" if data.accepted else "rejected"
            audit(session, vault, service, who.subject, "delivery_reconciled", visit, data.model_dump())
        return {"state": job.state}

    class Receipt(Input):
        delivered: bool
        provider_reference: str = Field(min_length=3, max_length=150)

    @app.post("/api/integration/services/{service_id}/jobs/{job_id}/receipt")
    def receipt(service_id: str, job_id: str, data: Receipt, who: Actor = Depends(actor)):
        with db.transaction(service_id) as session:
            service = service_for(session, service_id)
            who.require(service, {"integration"})
            job = session.get(Job, job_id)
            if not job or job.service_id != service_id:
                raise HTTPException(404, "Delivery not found")
            if job.kind not in {"notification", "verification_code"} or job.state not in {"accepted", "unknown"}:
                raise HTTPException(409, "This delivery cannot receive a receipt")
            job.state = "delivered" if data.delivered else "rejected"
            visit = session.get(Visit, job.visit_id) if job.visit_id else None
            if not data.delivered and visit and visit.state not in TERMINAL and not visit.contact_needed:
                escalate(session, vault, service, visit, "integration", "Patient message failed; telephone follow-up required")
            audit(session, vault, service, who.subject, "delivery_receipt", visit,
                  {"job_id": job.id, "delivered": data.delivered, "provider_reference": data.provider_reference})
        return {"state": job.state}

    @app.get("/api/staff/services/{service_id}/audit")
    def audit_history(service_id: str, who: Actor = Depends(actor)):
        with db.read() as session:
            service = service_for(session, service_id)
            who.require(service, {"admin", "auditor", "clinician"})
            return {"valid": verify_audit(session, vault, service), "head": service.audit_head,
                    "sequence": service.audit_seq,
                    "events": [{"sequence": e.sequence, "actor": e.actor, "kind": e.kind,
                                "visit_id": e.visit_id, "at": e.created, "digest": e.digest}
                               for e in session.scalars(select(Audit).where(Audit.service_id == service_id)
                                                        .order_by(Audit.sequence.desc()).limit(200))]}

    @app.get("/api/staff/services/{service_id}/metrics")
    def metrics(service_id: str, who: Actor = Depends(actor)):
        with db.read() as session:
            service = service_for(session, service_id)
            who.require(service, OPERATORS | {"auditor"})
            visits = list(session.scalars(select(Visit).where(Visit.service_id == service_id,
                                           Visit.created >= time.time() - 7 * 86400)))
            treated = [v for v in visits if v.treatment_at]
            on_site = [v for v in treated if v.arrived_at]
            return {"period_days": 7, "requests": len(visits), "treated": len(treated),
                    "needs_follow_up": sum(v.contact_needed for v in visits if v.state not in TERMINAL),
                    "mean_total_minutes_to_care": round(sum((v.treatment_at - v.first_contact) / 60 for v in treated) / len(treated), 1) if treated else None,
                    "mean_minutes_on_site_before_care": round(sum((v.treatment_at - v.arrived_at) / 60 for v in on_site) / len(on_site), 1) if on_site else None}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    @app.get("/patient")
    @app.get("/staff")
    def index():
        return FileResponse(STATIC / "index.html")

    return app
