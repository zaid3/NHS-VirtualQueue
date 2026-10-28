# Deployment, security and recovery

Do not use the local synthetic installation for patients. This document describes operations that a deploying organisation must implement and verify.

## Release process

1. Choose an approved hosting environment and domain. Use a private PostgreSQL service with certificate-verified TLS, encrypted storage, backups and least-privilege database accounts. Pin the tested container image and dependency versions in your release manifest.
2. Supply persistent encryption/signing keys, gateway HMAC key and mTLS certificates from the organisation's secret manager. Never commit `production.env`, certificate private keys or databases. Restrict container, host and operator access to those secrets.
3. Configure an approved OIDC provider and server-side assignments for `vq_tenant` and `vq_role`. Register the exact HTTPS `/auth/callback` URL. Production human sessions and human API tokens require an `amr` containing `mfa`. Service identities need the restricted integration role and short-lived, scoped access tokens.
4. Implement and test the [trust gateway contract](gateway-contract.md). Provision receiving service records, approved service contact numbers, capacity feeds, message wording, delivery receipts and a staffed reconciliation process. Tokens and PDS/BaRS access must be obtained through the permitted NHS onboarding route.
5. Complete the actual clinical and deployment release review. Set `CLINICAL_RELEASE_ID` to its record only after approval. The application checks that a reference exists; it cannot verify that an external approval is genuine.
6. Back up and test restoration before migrations. Run `alembic upgrade head` once with an authorised migration role, then start API and worker processes using a restricted runtime database role. Reconcile unknown deliveries after restarts.

`deploy/compose.yaml` supplies separate API, worker, migration and TLS edge services. It expects `deploy/production.env`, files under `deploy/certificates`, and `PUBLIC_HOST` supplied to Compose. Certificates must be readable by UID 10001 without being publicly readable. Replace this template with the organisation's orchestration and availability design where required. It is not a high-availability cluster or a complete cloud security configuration.

An example controlled deployment command, after the above work:

```bash
PUBLIC_HOST=queue.approved-domain.example docker compose -f deploy/compose.yaml up --build -d
```

The API is exposed only through the TLS edge. Do not publish its internal HTTP port. Use network policy to limit database access and outbound destinations to the approved IdP, gateway and, when enabled, Google Routes. The default template leaves worker supervision and external monitoring to the operator. Production does not refresh hospital capacity synthetically.

## Access and privacy

| Role | Allowed actions |
| --- | --- |
| Patient | Own episode, own journey and symptom/cancellation messages |
| Clinician | Tenant care workspace, clinical decisions, episode transitions, pause/resume and audit |
| Coordinator | Tenant care workspace, assisted intake and recording arrival; no clinical decisions |
| Administrator | Tenant service configuration, operational workspace, pause/resume and audit; no clinician decisions |
| Auditor | Tenant audit, operational metrics and delivery metadata; no patient list or clinical mutation |
| Integration | Assigned tenant capacity, assisted referrals, demographics and delivery reconciliation; no browser sign-in |

Coordinators and administrators can see operational patient details and patient-facing clinical instructions, but the full assessment text is restricted to clinicians. Each deployment must validate this allocation against its access policy. No end user may assign their own claims. Remove staff access at the identity provider and revoke associated database sessions when access ends. Session duration is 30 minutes and never exceeds the ID token expiry.

The app encrypts identity, clinical notes, challenge payloads, delivery payloads and audit details. Identifiers, timestamps, states and keyed telephone hashes remain queryable. Treat the whole database as sensitive; field encryption is not database anonymisation. Do not put PHI into APM traces, exception telemetry, proxy URLs or HTTP body logging. External services have their own retention and access controls.

No nonessential browser analytics or marketing cookies are included. Authentication uses secure HttpOnly cookies in production and CSRF/origin checks for changes. The app does not store credentials in localStorage. Patient geolocation is optional, used only on request for Google Routes, and not stored in the app database. Approve that disclosure and vendor arrangement before enabling the key.

Set a documented retention schedule with the controller. This release has no automatic deletion of clinical episodes or audit records. Apply a controlled retention/export process that preserves clinical accountability, legal holds and dependencies. The worker removes expired verification challenges, sessions, OIDC flows and rate-limit entries, and redacts verification delivery payloads after six minutes. Backup retention still needs a policy. Never use a public repository as an evidence store for patient information.

## Monitoring and downtime

`GET /health/live` reports that the process responds. `/health/ready` checks the expected migration revision and database. Neither proves that patients are being assessed or that integrations work. Monitor worker restarts and logs, oldest pending/sending/unknown jobs, overdue callbacks, follow-up flags, capacity feed age, message receipts and review/arrival deadlines. Alert a staffed team; a dashboard that nobody watches is not a safety control.

Five minutes without a valid capacity feed removes current waiting estimates. The watchdog requests review; it never manufactures capacity. Unknown deliveries must be checked against the receiving system, then reconciled using the integration API. Do not reset them to pending or replay them casually. More workers can claim independent jobs safely, but service-level write locks deliberately serialise related mutations. Establish capacity limits through load testing, not assumption.

During an outage stop new remote-waiting offers, contact affected patients through the established care pathway and use the existing EPR/PAS downtime process. Patients must continue to follow their clinical instructions and use urgent/emergency care when needed. The app does not cache health information for offline use. Browser errors suppress live estimates; printing creates a sensitive, immediately ageing copy that the patient must protect.

## Audit, backup and keys

Run `virtualqueue audit --service SERVICE_ID` on a schedule and record the sequence/head in separately controlled append-only storage. The HMAC chain detects changes relative to its key and checkpoint; a privileged party with both the database and signing key could rewrite it. This is not a blockchain or an independently immutable audit system.

Back up encrypted database snapshots and the corresponding encryption keys separately. Test restoration into an isolated environment with external delivery disabled. Compare audit checkpoints and receiving-system booking records before restarting workers. Define and rehearse recovery time and recovery point objectives with the trust; no tested values are claimed here.

Key rotation is a controlled maintenance operation in this release. There is no automatic multi-key migration. Pause writes/delivery, back up, verify the existing chain, re-encrypt all encrypted columns under a new key with a reviewed migration, rotate relevant secrets, and verify restore/rollback before resuming. Signing-key changes affect audit verification, session hashes and telephone matching: do not simply replace the environment variable. A production deployment must implement and test its rotation tooling before launch.

## Release gates still requiring external evidence

Independent penetration testing, accessibility assessment, patient/staff usability validation, load and resilience testing at agreed capacity, backup restoration rehearsal, clinical safety sign-off, data protection review, NHS API assurance, receiving-system contract tests and an approved operational rota. Automated unit tests and a vulnerability scan do not replace these activities.
