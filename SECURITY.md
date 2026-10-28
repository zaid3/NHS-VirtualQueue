# Security

NHS VirtualQueue is an independent project maintained by A Haque (zaid). No penetration test, NHS security certification or clinical approval is claimed for this release.

Do not put patient data, credentials, tokens or reproduction details for an exploitable vulnerability in public issues. Use GitHub's private vulnerability reporting facility if the repository offers it. If unavailable, request a private contact route from the maintainer without publishing sensitive details. Production adopters must provide their own incident contact and response rota.

The application uses encrypted patient and clinical fields, organisation-scoped authorisation, short-lived server-side sessions, CSRF checks, OIDC signature validation, staff MFA in production and an authenticated gateway. These controls reduce particular risks; they do not make a system unbreakable.

Scan dependencies and containers for every release. Pin container and action digests in the deploying organisation's release process. Rebuild patched images; a clean vulnerability scan reflects only the databases available when it ran.

See [operations](docs/operations.md) for keys, access, audit and recovery, and [the hazard register](docs/hazards.csv) for patient-safety risks.
