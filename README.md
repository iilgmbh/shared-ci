# shared-ci

Zentrale, **versionierte** Reusable-Workflows + Composite-Actions des IIL-Ökosystems
(OOTB-A, KONZ-platform-002). Aus `platform` herausgelöst, damit `platform` (und jedes
andere Repo) frei umziehen kann, ohne `uses:`-Refs zu brechen.

**Konsumenten pinnen auf einen Tag** (`@v1.0.0`), NICHT `@main` (Supply-Chain).
Updates laufen über Dependabot (`github-actions`). Design: `platform/docs/runbooks/KONZ-002-ootb-a-shared-ci.md`.
