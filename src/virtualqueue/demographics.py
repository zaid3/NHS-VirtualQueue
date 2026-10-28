import re
import uuid
from urllib.parse import urlparse

import httpx


def valid_nhs_number(value):
    value = re.sub(r"\s", "", value or "")
    if not re.fullmatch(r"[0-9]{10}", value) or value == "0000000000":
        return False
    check = 11 - sum(int(n) * w for n, w in zip(value[:9], range(10, 1, -1))) % 11
    return check != 10 and (0 if check == 11 else check) == int(value[-1])


def postcode_key(value):
    return re.sub(r"\s", "", value).upper()


class PDSClient:
    """Read-only demographic tracing transport for a permitted trust workflow.

    This does not authenticate a patient, grant access to clinical records,
    register patients, or implement NHS login/CIS2 onboarding.
    """

    def __init__(self, base_url, token_provider, transport=None, end_user_organisation=None):
        if urlparse(base_url).scheme != "https":
            raise ValueError("PDS requires HTTPS")
        self.base_url, self.token_provider, self.transport = base_url.rstrip("/"), token_provider, transport
        self.end_user_organisation = end_user_organisation

    def exact_candidate(self, family_name, birth_date, postcode):
        if any(c in family_name + postcode for c in "*?~"):
            raise ValueError("Wildcard demographic searches are not supported")
        params = {"family": family_name, "birthdate": "eq" + str(birth_date),
                  "address-postalcode": postcode, "_exact-match": "true"}
        headers = {"Authorization": "Bearer " + self.token_provider(),
                   "X-Request-ID": str(uuid.uuid4()), "Accept": "application/fhir+json"}
        if self.end_user_organisation:
            headers["NHSD-End-User-Organisation-ODS"] = self.end_user_organisation
        with httpx.Client(timeout=10, transport=self.transport, follow_redirects=False) as client:
            response = client.get(self.base_url + "/Patient", params=params,
                                  headers=headers)
            response.raise_for_status()
            bundle = response.json()
        entries = [e.get("resource", {}) for e in bundle.get("entry", [])
                   if e.get("resource", {}).get("resourceType") == "Patient"]
        if bundle.get("resourceType") != "Bundle" or bundle.get("total") != 1 or len(entries) != 1:
            return {"status": "manual_review", "reason": "no_unique_match"}
        if any(e.get("search", {}).get("score") != 1 for e in bundle.get("entry", [])):
            return {"status": "manual_review", "reason": "match_not_exact"}
        patient = entries[0]
        if patient.get("meta", {}).get("security") or patient.get("deceasedBoolean") or patient.get("deceasedDateTime"):
            return {"status": "manual_review", "reason": "restricted_or_inconsistent_record"}
        if patient.get("birthDate") != str(birth_date):
            return {"status": "manual_review", "reason": "demographics_differ"}
        if not any(postcode_key(a.get("postalCode", "")) == postcode_key(postcode) for a in patient.get("address", [])):
            return {"status": "manual_review", "reason": "demographics_differ"}
        if not any(n.get("family", "").casefold() == family_name.casefold() for n in patient.get("name", [])):
            return {"status": "manual_review", "reason": "demographics_differ"}
        nhs = patient.get("id", "")
        if not valid_nhs_number(nhs):
            return {"status": "manual_review", "reason": "invalid_identifier"}
        return {"status": "candidate", "nhs_number": nhs, "etag": response.headers.get("etag")}
