# Trust gateway contract, version 1

This is a VirtualQueue integration contract, **not an NHS API specification**. A deployment-owned gateway must translate it into the participating service's approved EPR/PAS, booking and messaging interfaces. None is silently inferred from a provider's name.

## Outbound delivery

The worker sends `POST {TRUST_GATEWAY_URL}/v1/events` over verified HTTPS with a client certificate. The body is canonical JSON:

```json
{
  "id": "durable-job-uuid",
  "service_id": "configured-service-id",
  "kind": "booking",
  "payload": {
    "visit_id": "episode-uuid",
    "version": 3,
    "slot_reference": "receiving-system-slot-id",
    "start": 1793188800,
    "end": 1793190000,
    "assessment_reference": "local-EPR-reference",
    "patient": {"name": "Synthetic Example", "family_name": "Example", "birth_date": "1990-06-02", "postcode": "E6 1AA", "phone": "+447700900123"}
  }
}
```

Times are UTC Unix seconds. Examples contain synthetic values. JSON canonicalisation sorts keys, uses compact separators and ASCII escaping. Headers include:

* `Idempotency-Key`: the stable job UUID.
* `X-VQ-Timestamp`: integer UTC Unix seconds.
* `X-VQ-Signature`: lowercase hex HMAC-SHA256 of `timestamp + "." + exact_request_body`, using `TRUST_GATEWAY_KEY`.

The gateway must verify the client certificate, body signature and a maximum 60-second timestamp difference, maintain durable idempotency records, and reject reuse of a key with different content. It must never execute a duplicate booking. Authenticate and scope the gateway separately for each organisation.

## Event meanings

| Kind | Payload | Required behaviour |
| --- | --- | --- |
| `booking` | Patient details, source assessment, exact slot, episode/version | Verify local record and suitability of the receiving pathway; atomically reserve the specified window or reject |
| `cancel_booking` | External booking reference, intended local next state and version | Cancel that booking once; return accepted only after confirmed cancellation |
| `verification_code` | Telephone, six-digit code, expiry | Deliver the generic verification message; never send after expiry |
| `notification` | Telephone, generic purpose, episode/version | Send approved generic wording with the fixed public portal URL; no diagnosis, clinical notes, OTP or bearer token in the URL |

Notification purposes include `arrival_confirmed`, `departure_reminder`, `review_required` and `attend_now`. The gateway must use clinically approved wording, accessible alternatives and a safe contact route. A web portal message must not be the sole emergency contact channel. Receipt of SMS does not prove it was read.

Do not automatically change a booking's window. Return `rejected` and let the care team assess an alternative. Do not forward arbitrary patient input to URLs, executable templates or unvalidated FHIR extensions. Construct the required BaRS resources from the verified EPR record, including address or other mandatory fields absent from the minimal portal intake.

## Response and uncertainty

```json
{"status": "accepted", "reference": "durable-receiving-system-reference"}
```

Supported statuses are `accepted`, `delivered`, `rejected`, and `pending`. `accepted` and `delivered` need a nonempty durable reference. For a booking, `accepted` means the specified booking actually exists; for cancellation it means the specified booking is cancelled. A gateway must return `pending` while it only has transport acknowledgement.

Timeouts, errors and `pending` become `unknown`, trigger follow-up where a visit exists, and are not retried blindly. A sending job abandoned for 60 seconds also becomes unknown. Staff must contact the receiving system and an authorised integration process must reconcile the recorded outcome. This conservative behaviour avoids duplicate bookings; it requires an operational reconciliation team.

`POST /api/integration/services/{service}/jobs/{job}/reconcile` accepts `accepted`, `external_reference`, and a reason. It acts only on unknown jobs. A late booking acceptance cannot restore expired permission to wait remotely. A late cancellation cannot close a patient who has since reported worsening symptoms.

For messages, `POST .../jobs/{job}/receipt` records `delivered` and a `provider_reference`. Accepted patient notifications without a receipt after 180 seconds require follow-up. A late successful receipt does not clear a clinical review alert.

Integration callbacks use signed access tokens from the trusted provider: audience `virtualqueue`, scope `virtualqueue`, `token_use=access`, `vq_role=integration`, and the assigned `vq_tenant`. Every service is checked against that tenant. Browser cookies or unauthenticated webhooks are insufficient.

## Capacity and demographics

`POST /api/integration/services/{service}/capacity` accepts `version`, `observed_at`, `paused` and a complete current `slots` list. Each slot has an external reference, UTC-aware start/end, capacity and enabled flag. Older versions, duplicate references, stale observations and moving a reserved slot are rejected. An omitted slot is disabled. Reducing capacity below reservations causes clinical follow-up rather than silently dropping a patient.

The demographics endpoint records a result of the trust's approved identity workflow. It is not permission to expose PDS to the public. See [NHS integration](nhs-integration.md).

## Contract acceptance tests required at each trust

Test valid and invalid certificates/signatures, replay, duplicate keys, changed payload, gateway timeout after success, receiver rejection, acknowledgement without booking, double cancellation, stale capacity, record mismatch, notification failure and out-of-order callbacks. Exercise the real receiving system in its approved integration environment. The repository's mock transport tests do not replace these tests.
