# shared-ci

Zentrale, **versionierte** Reusable-Workflows + Composite-Actions des IIL-Ökosystems
(OOTB-A, KONZ-platform-002). Aus `platform` herausgelöst, damit `platform` (und jedes
andere Repo) frei umziehen kann, ohne `uses:`-Refs zu brechen.

**Konsumenten pinnen auf einen Tag** (`@v1.0.0`), NICHT `@main` (Supply-Chain).
Updates laufen über Dependabot (`github-actions`). Design: `platform/docs/runbooks/KONZ-002-ootb-a-shared-ci.md`.

**Auto-Release** (`auto-release.yml`, täglich): grüne Dependabot-Bumps fremder Actions
ohne Hauptversions-Sprung werden gemergt, danach wird der nächste Patch-Tag gesetzt —
aber nur, wenn zwischen Tag und `main` ausschließlich solche Versionszeilen liegen.
Alles andere bleibt liegen und nennt den Grund im Lauf-Protokoll (platform#3775).
