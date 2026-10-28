# Validation record

Prepared 28 October 2026 for version 0.1.0. All records, external responses and identity-provider fixtures used here are synthetic.

## Local checks

* The Python workflow/security/integration suite passed 74 tests on SQLite with 83.71% statement coverage. Re-run `pytest --cov=virtualqueue --cov-report=term-missing --cov-fail-under=80` to validate a checkout.
* `ruff check .` passed.
* Runtime dependency scan using `pip-audit -r requirements.lock` reported no known vulnerabilities on the scan date. This is not a penetration test or an assurance of no vulnerabilities.
* Alembic migration `0001` was applied successfully. Test fixtures recreate the schema from this migration for each integration test.
* The application runs locally with a real persistent database, API, worker and browser assets. External bookings and messaging are simulated in local mode.

## Scenarios covered by automated tests

Patient request and telephone verification; OTP expiry, replay, lockout and rate limiting; authentication, CSRF, origin and host checks; session and tenant boundaries; sensitive field encryption; validation redaction; production configuration gates; signed OIDC tokens, nonce, PKCE, audience, expiry and MFA; clinician-only assessment; high-priority remote-wait exclusion; competing reservation of the final slot; unknown booking outcomes; no blind retries; stale capacity and overdue review; worsening during cancellation; preservation of in-person care status; travel time exceeding a clinical window; delayed message receipts; audit tampering; exact PDS candidate matching; demographic mismatch; BaRS transport headers; travel API modes and invalid durations; OTP payload maintenance.

## Separate environment checks

[GitHub Actions run 40787878859](https://github.com/zaid3/NHS-VirtualQueue/actions/runs/40787878859) passed on commit `d8b662eeffab4a0a93481810db70833338aec3ae`:

| Check | Verified result |
| --- | --- |
| SQLite workflow/security tests | Passed |
| PostgreSQL 16 workflow/security tests | 74 passed |
| Chromium patient and clinician journey | Passed; no page errors |
| Responsive mobile layout and offline fallback | Passed in the browser journey |
| Python lint, dependency scan and wheel packaging | Passed |
| Docker image build | Passed |

The four browser screenshots were downloaded and the desktop homepage, staff dashboard and mobile patient screen were visually reviewed. The synthetic UI remains clearly labelled as unconnected to NHS services. See [Actions](https://github.com/zaid3/NHS-VirtualQueue/actions) for subsequent commit results; evidence for one commit does not automatically apply to every future revision.

The local environment could not run PostgreSQL under a non-root account or launch Chromium because the necessary process/socket operations were denied. Those checks were instead completed successfully on GitHub runners; no local PostgreSQL or browser execution pass is claimed. The browser script is `scripts/browser_smoke.py`; it exercises patient intake, clinician assessment, booking confirmation, mobile overflow, worsening and offline fallback, and writes synthetic screenshots to `artifacts/`.

On a machine with permitted browser process support:

```bash
python -m playwright install --with-deps chromium
python scripts/browser_smoke.py
```

`VQ_BROWSER` can select an already installed Chromium executable. Browser screenshots contain synthetic fixtures only. The script uses a temporary database and destroys it on completion.

For PostgreSQL tests, set `TEST_DATABASE_URL` to a disposable database. **Every test drops its tables. Never supply a production database.**

## What this evidence does not establish

No live PDS, BaRS, NHS login/CIS2, NHS EPR/PAS, hospital booking service, SMS service or Google Routes integration has been exercised. Transport-level mock tests do not prove NHS conformance. There is no measured clinical benefit, real patient trial, accessibility certification, independent penetration test, load-test capacity claim, disaster-recovery result or signed clinical safety case.

The deployment template requires review and integration testing in the adopting environment. Do not label a release clinically production-ready solely because its automated checks pass. The [innovation brief](innovation-submission.md) distinguishes technical evidence from benefits still requiring evaluation.
