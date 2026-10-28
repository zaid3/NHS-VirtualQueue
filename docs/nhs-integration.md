# NHS integration and patient identity

Reviewed against official sources on 28 October 2026. The first intended integration scope is a participating urgent care service in England. Other nations, providers and clinical pathways require their own discovery and assurance.

## System responsibilities

| System | Responsibility | Current project support |
| --- | --- | --- |
| Existing EPR/PAS | Clinical record, local patient identity, encounters, clinical decisions | References and trust gateway boundary; supplier mapping not implemented for a named EPR |
| Personal Demographics Service (PDS) | National demographic tracing and NHS number | Read-only exact-candidate transport and checksum checks; no live connection |
| Booking and Referral Standard (BaRS) | Supported booking/referral exchanges between participating systems | FHIR transport, target headers and message envelope checks; full use-case profiling and assurance outstanding |
| Care service capacity feed | Service-specific arrival windows and availability | Authenticated, versioned feed with stale-data detection |
| Organisation identity provider | Staff authentication, MFA and assigned roles | OIDC discovery, PKCE, signed tokens, nonce, audience and tenant/role validation |
| Messaging provider | OTP and patient notifications | Durable outbound contract and receipt endpoint through trust gateway |

NHS login and Care Identity Service 2 are not implemented substitutes for the generic OIDC provider. Use the appropriate approved identity route for each NHS API access mode. A trust's application-restricted PDS access must not be exposed as an unrestricted public patient search.

## DOB and postcode flow

The portal collects a person's name, family name, date of birth, postcode and contact number. Telephone verification opens only that person's VirtualQueue episode. It does not open NHS clinical records or authenticate NHS identity.

An authorised trust workflow can call `PDSClient.exact_candidate`. The request includes family name, exact birth date and full postcode. The client accepts only one exact, unrestricted, internally consistent candidate with a valid NHS number checksum. A candidate still requires the trust's identity verification and local record reconciliation. Missing, multiple, low-score, restricted, deceased or mismatched results produce manual review rather than an automatic link.

For multi-organisation PDS access, pass the correct `end_user_organisation` ODS code. Credentials, permitted access mode, user context and audit requirements must be agreed during NHS onboarding. The client does not obtain NHS access tokens or synchronise a master patient record. The EPR/PAS remains responsible for its PDS synchronisation obligations.

After verification, the gateway can call `POST /api/integration/services/{service}/visits/{visit}/demographics`. This requires the integration role, matching tenant, current version and matching submitted demographics. It accepts a checked NHS number or the receiving service's local record reference. The result is encrypted, audited and not returned in the patient status response.

Patients without a stable postcode, telephone, NHS number or successful trace must not be excluded from care. Use the service's existing registration and assisted-care pathway. This release does not implement proxy accounts, paediatric consent, unidentified-patient registration or PDS record creation. A shared contact number is limited to one active episode per service; staff must use existing systems for family/proxy cases until a separately assessed proxy workflow exists.

## Booking and capacity

The hospital publishes arrival windows, capacity, an observation timestamp and a monotonic version. These represent a specific clinical service, not spare capacity across an entire hospital. The feed must represent total capacity consistently with existing reservations and other booking channels; the receiving system makes the final acceptance decision.

A clinician records a telephone assessment, identity verification, the clinical record reference, clinical instructions, priority, reassessment time and attendance limit. A local reservation creates `booking_pending`. Only explicit acceptance of the exact requested window by the receiving service permits `remote_waiting`. A BaRS HTTP 202 or message receipt alone is insufficient. Cancellation also requires an external acknowledgement before releasing a confirmed booking.

`BaRSClient` sends FHIR JSON using `NHSD-Target-Identifier`, request IDs and integration-supplied requesting headers. Profile validation, supported use case, requesting organisation/software/practitioner information, routing, token acquisition and provider mapping belong to the assured sender implementation. There is no tested end-to-end BaRS connection in this release. Confirm the current catalogue/specification version with the onboarding team: the public catalogue and downloaded specifications can contain different status wording.

Recorded visits ahead are approximate. They exclude unrecorded arrivals and cannot determine A&E order. All patients still need the receiving service's required assessment. Ambulance pre-alerts must follow existing emergency communication channels; this application is not an ambulance dispatch system.

## Sources

* [PDS overview](https://digital.nhs.uk/services/personal-demographics-service)
* [PDS integration guidance](https://digital.nhs.uk/services/personal-demographics-service/integration-guidance)
* [PDS FHIR API](https://digital.nhs.uk/developer/api-catalogue/personal-demographics-service-fhir)
* [BaRS overview](https://digital.nhs.uk/services/booking-and-referral-standard)
* [BaRS API](https://digital.nhs.uk/developer/api-catalogue/booking-and-referral-fhir)
* [BaRS compliance and governance](https://digital.nhs.uk/services/booking-and-referral-standard/compliance-and-governance)
* [Emergency department initial assessment](https://www.england.nhs.uk/guidance-for-emergency-departments-initial-assessment/)
