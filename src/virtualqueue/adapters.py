"""Explicit integration boundaries; no connector is represented as NHS-assured."""

import base64
import hashlib
import hmac
import math
import ssl
import time
import uuid
from urllib.parse import urlparse

import httpx

from .security import canonical


class TrustGateway:
    """Trust-owned gateway implements docs/gateway-contract.md and idempotent delivery."""

    def __init__(self, settings, transport=None):
        self.settings, self.transport = settings, transport

    def send(self, job, payload):
        if self.settings.mode != "production" and not self.settings.gateway_url:
            if job.kind == "booking":
                return {"status": "accepted", "reference": "SYNTHETIC-" + job.id}
            return {"status": "simulated", "reference": "SYNTHETIC-" + job.id}
        if urlparse(self.settings.gateway_url).scheme != "https":
            raise ValueError("Gateway must use HTTPS")
        context = ssl.create_default_context(cafile=self.settings.gateway_ca or None)
        if self.settings.gateway_cert:
            context.load_cert_chain(self.settings.gateway_cert, self.settings.gateway_private_key)
        body = canonical({"id": job.id, "service_id": job.service_id, "kind": job.kind, "payload": payload})
        timestamp = str(int(time.time()))
        signature = hmac.new(self.settings.gateway_key.encode(), (timestamp + "." + body).encode(),
                             hashlib.sha256).hexdigest()
        with httpx.Client(timeout=httpx.Timeout(10, connect=5), verify=context, transport=self.transport,
                          follow_redirects=False) as client:
            response = client.post(self.settings.gateway_url.rstrip("/") + "/v1/events", content=body,
                headers={"Content-Type": "application/json", "Idempotency-Key": job.id,
                         "X-VQ-Timestamp": timestamp, "X-VQ-Signature": signature})
            response.raise_for_status()
            result = response.json()
        if result.get("status") not in {"accepted", "delivered", "rejected", "pending"}:
            raise ValueError("Unknown gateway response")
        if result["status"] in {"accepted", "delivered"} and not result.get("reference"):
            raise ValueError("Gateway did not provide a durable reference")
        return result


class BaRSClient:
    """FHIR transport for a trust adapter, using already-issued NHS access tokens.

    Payload profiling, OAuth onboarding and receiving-system mapping belong to
    the assured sender. A 202 response is NOT a confirmed clinical booking.
    """

    def __init__(self, base_url, token_provider, target_identifier, requesting_headers=None, transport=None):
        if urlparse(base_url).scheme != "https":
            raise ValueError("BaRS requires HTTPS")
        self.base_url, self.token_provider = base_url.rstrip("/"), token_provider
        self.target_identifier = target_identifier
        self.requesting_headers, self.transport = requesting_headers or {}, transport

    def request(self, method, path, *, params=None, bundle=None, request_id=None):
        if path not in {"/Slot", "/Appointment", "/ServiceRequest", "/$process-message", "/metadata"}:
            raise ValueError("Unsupported BaRS operation")
        target = base64.b64encode(canonical(self.target_identifier).encode()).decode()
        headers = {**self.requesting_headers, "Authorization": "Bearer " + self.token_provider(),
                   "Accept": "application/fhir+json", "Content-Type": "application/fhir+json",
                   "NHSD-Target-Identifier": target, "X-Request-ID": request_id or str(uuid.uuid4())}
        if bundle is not None:
            if bundle.get("resourceType") != "Bundle" or bundle.get("type") != "message":
                raise ValueError("A FHIR message Bundle is required")
            resources = [e.get("resource", {}) for e in bundle.get("entry", [])]
            if not resources or resources[0].get("resourceType") != "MessageHeader":
                raise ValueError("The first resource must be MessageHeader")
        with httpx.Client(timeout=15, transport=self.transport, follow_redirects=False) as client:
            response = client.request(method, self.base_url + path, params=params, json=bundle, headers=headers)
            response.raise_for_status()
            return {"http_status": response.status_code, "body": response.json() if response.content else None}


class TravelEstimator:
    def __init__(self, key, transport=None):
        self.key, self.transport = key, transport

    def estimate(self, origin, destination, mode):
        if not self.key:
            raise ValueError("Live journey estimates are not configured. Enter your own estimate.")
        travel_mode = {"lift": "DRIVE", "taxi": "DRIVE", "walking": "WALK", "public_transport": "TRANSIT"}[mode]
        payload = {"origin": {"location": {"latLng": origin}},
                   "destination": {"location": {"latLng": destination}}, "travelMode": travel_mode}
        if travel_mode == "DRIVE":
            payload["routingPreference"] = "TRAFFIC_AWARE"
        with httpx.Client(timeout=8, transport=self.transport, follow_redirects=False) as client:
            response = client.post("https://routes.googleapis.com/directions/v2:computeRoutes", json=payload,
                                   headers={"X-Goog-Api-Key": self.key, "X-Goog-FieldMask": "routes.duration"})
            response.raise_for_status()
            seconds = float(response.json()["routes"][0]["duration"].removesuffix("s"))
        if not math.isfinite(seconds) or not 0 < seconds <= 14400:
            raise ValueError("Journey estimate is outside supported limits")
        return {"minutes": max(1, math.ceil(seconds / 60)), "source": "google_routes", "observed_at": time.time()}
