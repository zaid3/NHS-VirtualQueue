import base64
import json
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from virtualqueue.adapters import BaRSClient, TravelEstimator, TrustGateway
from virtualqueue.demographics import PDSClient, valid_nhs_number


@pytest.mark.parametrize("value,valid", [("9434765919", True), ("0000000000", False),
                                        ("1234567890", False), ("abc", False), (None, False)])
def test_nhs_number_checksum(value, valid):
    assert valid_nhs_number(value) is valid


@pytest.mark.parametrize("condition,expected", [("exact", "candidate"), ("multiple", "manual_review"),
    ("restricted", "manual_review"), ("postcode", "manual_review"), ("score", "manual_review"),
    ("deceased", "manual_review"), ("operation_outcome", "manual_review")])
def test_pds_returns_candidate_only_for_exact_unrestricted_match(condition, expected):
    patient = {"resourceType": "Patient", "id": "9434765919", "birthDate": "1990-06-02",
               "name": [{"family": "Example"}], "address": [{"postalCode": "E6 1AA"}]}
    if condition == "restricted":
        patient["meta"] = {"security": [{"code": "R"}]}
    if condition == "postcode":
        patient["address"] = [{"postalCode": "SW1A 1AA"}]
    if condition == "deceased":
        patient["deceasedBoolean"] = True
    def handler(request):
        assert request.url.params["birthdate"] == "eq1990-06-02"
        assert request.url.params["_exact-match"] == "true"
        assert request.headers["NHSD-End-User-Organisation-ODS"] == "TESTODS"
        bundle = {"resourceType": "Bundle", "total": 2 if condition == "multiple" else 1,
                  "entry": [{"resource": patient, "search": {"score": 0.9 if condition == "score" else 1}}]}
        return httpx.Response(200, json={"resourceType": "OperationOutcome"} if condition == "operation_outcome" else bundle)
    client = PDSClient("https://pds.example.org", lambda: "synthetic-token", httpx.MockTransport(handler), "TESTODS")
    assert client.exact_candidate("Example", date(1990, 6, 2), "E6 1AA")["status"] == expected


def test_pds_does_not_permit_wildcard_searches():
    client = PDSClient("https://pds.example.org", lambda: "token")
    with pytest.raises(ValueError):
        client.exact_candidate("Ex*", date(1990, 6, 2), "E6 1AA")


def test_bars_headers_and_accepted_is_not_confirmed_booking():
    target = {"system": "https://fhir.nhs.uk/Id/dos-service-id", "value": "2000011147"}
    def handler(request):
        assert json.loads(base64.b64decode(request.headers["NHSD-Target-Identifier"])) == target
        assert request.headers["Authorization"] == "Bearer synthetic"
        return httpx.Response(202, json={"resourceType": "OperationOutcome"})
    client = BaRSClient("https://bars.example.org/FHIR/R4", lambda: "synthetic", target, transport=httpx.MockTransport(handler))
    result = client.request("POST", "/$process-message", bundle={"resourceType": "Bundle", "type": "message",
                              "entry": [{"resource": {"resourceType": "MessageHeader"}}]})
    assert result["http_status"] == 202 and "confirmed" not in result
    with pytest.raises(ValueError):
        client.request("POST", "/$process-message", bundle={"resourceType": "Patient"})
    with pytest.raises(ValueError):
        client.request("GET", "/../../secret")


@pytest.mark.parametrize("mode,expected", [("lift", "DRIVE"), ("walking", "WALK"), ("public_transport", "TRANSIT")])
def test_route_modes_and_seconds_rounded_up(mode, expected):
    def handler(request):
        data = json.loads(request.content)
        assert data["travelMode"] == expected
        assert ("routingPreference" in data) == (expected == "DRIVE")
        assert request.headers["X-Goog-FieldMask"] == "routes.duration"
        return httpx.Response(200, json={"routes": [{"duration": "721s"}]})
    estimator = TravelEstimator("synthetic", httpx.MockTransport(handler))
    assert estimator.estimate({"latitude": 51, "longitude": 0}, {"latitude": 51.1, "longitude": 0}, mode)["minutes"] == 13


@pytest.mark.parametrize("duration", ["NaNs", "-1s", "99999s"])
def test_route_rejects_unsafe_duration(duration):
    estimator = TravelEstimator("test", httpx.MockTransport(lambda _: httpx.Response(200, json={"routes": [{"duration": duration}]})))
    with pytest.raises(ValueError):
        estimator.estimate({}, {}, "lift")


def test_gateway_signed_idempotent_and_no_redirect(env):
    from dataclasses import replace
    settings = replace(env[3], gateway_url="https://gateway.example.org", gateway_key="k" * 40)
    def handler(request):
        assert request.headers["Idempotency-Key"] == "delivery-1"
        assert len(request.headers["X-VQ-Signature"]) == 64
        assert json.loads(request.content)["payload"] == {"test": True}
        return httpx.Response(200, json={"status": "accepted", "reference": "external-1"})
    gateway = TrustGateway(settings, httpx.MockTransport(handler))
    assert gateway.send(SimpleNamespace(id="delivery-1", service_id="s", kind="booking"), {"test": True})["status"] == "accepted"
    gateway = TrustGateway(settings, httpx.MockTransport(lambda _: httpx.Response(302, headers={"Location": "https://evil.invalid"})))
    with pytest.raises(httpx.HTTPStatusError):
        gateway.send(SimpleNamespace(id="delivery-1", service_id="s", kind="booking"), {})
