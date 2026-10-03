# shared-ci

Zentrale, **versionierte** Reusable-Workflows + Composite-Actions des IIL-Ökosystems
(OOTB-A, KONZ-platform-002). Aus `platform` herausgelöst, damit `platform` (und jedes
andere Repo) frei umziehen kann, ohne `uses:`-Refs zu brechen.

**Konsumenten pinnen auf einen Tag** (`@v1.0.0`), NICHT `@main` (Supply-Chain).
Updates laufen über Dependabot (`github-actions`). Design: `platform/docs/runbooks/KONZ-002-ootb-a-shared-ci.md`.

## Setup — wie ein Repo diese Workflows einbindet

### 1. Aufruf

Ein Reusable-Workflow wird als **Job** eingebunden, nicht als Step:

```yaml
jobs:
  ci:
    uses: iilgmbh/shared-ci/.github/workflows/_ci-python.yml@v1.1.17
    with:
      django_settings_module: "config.settings.test"
      coverage_threshold: 80
    secrets:
      PROJECT_PAT: ${{ secrets.PROJECT_PAT }}
```

Der Tag hinter `@` ist Pflicht. `@main` bricht dir die Pipeline, sobald hier
etwas umgebaut wird, und hebelt die Lieferketten-Sicherung aus.

### 2. Secrets: `inherit` reicht über Organisationsgrenzen NICHT

Das ist die Falle, die am meisten Zeit kostet, weil sie **lautlos** ist.

GitHub gibt Secrets per `secrets: inherit` nur an Reusable-Workflows **derselben
Organisation oder Enterprise** weiter. Dieses Repo liegt in `iilgmbh`. Jedes
aufrufende Repo unter `achimdehnert`, `meiki-lra` oder `ttz-lif` überquert damit
eine Organisationsgrenze — und bekommt hier **nichts**, ohne dass irgendwo ein
Fehler erscheint.

Gemessen am 2026-09-07: in einem Staging-Job kamen `INPUT_HOST`, `INPUT_USERNAME`,
`INPUT_KEY` und `INPUT_FINGERPRINT` leer an, obwohl die Werte im aufrufenden Repo
gesetzt waren. Der Job scheiterte erst an der SSH-Verbindung, nicht an der
Übergabe. Ein Vergleichslauf aus einem `iilgmbh`-Repo zeigte dieselben Felder
gefüllt.

**Der Fehler zeigt sich erst, wenn die Vorlage ein Secret wirklich braucht.**
Ein Aufruf mit `secrets: inherit`, der heute grün läuft, kann trotzdem
falsch verdrahtet sein — er nutzt das Secret bislang nur nicht.

Richtig ist die explizite Liste:

```yaml
    secrets:
      DEPLOY_SSH_KEY: ${{ secrets.DEPLOY_SSH_KEY }}
      DEPLOY_HOST: ${{ secrets.DEPLOY_HOST }}
      DEPLOY_USER: ${{ secrets.DEPLOY_USER }}
      DEPLOY_SSH_FINGERPRINT: ${{ secrets.DEPLOY_SSH_FINGERPRINT }}
```

Hintergrund und Bestandsaufnahme: [platform#2922](https://github.com/achimdehnert/platform/issues/2922).

### 3. Welche Vorlage wofür

Vorlagen mit `_`-Präfix werden aufgerufen, die übrigen laufen als eigenständige
Gates im aufrufenden Repo.

| Vorlage | Zweck | Erwartete Secrets |
|---|---|---|
| `_ci-python.yml` | Tests, Abdeckungsschwelle, `pip-audit` | `PROJECT_PAT` (optional) |
| `_ci-odoo.yml` | Odoo-spezifische CI | `PIP_INDEX_URL` (optional) |
| `_ci-pypi.yml` | Paketbau und Veröffentlichung über OIDC | keine |
| `_build-docker.yml` | Image bauen, Trivy-Scan, GHCR-Push | keine |
| `_deploy-unified.yml` | Auslieferung nach Staging oder Produktion | `DEPLOY_*`, `STAGING_*` |
| `_deploy-hetzner.yml` | Auslieferung auf einen Hetzner-Host | `DEPLOY_*` |
| `deploy-config-lint.yml` | Prüft Deploy-Konfiguration | keine |
| `doc-profile-guard.yml` | Prüft Doku-Profil | keine |
| `handoff-banner-gate.yml` | Prüft Übergabe-Banner | keine |
| `silent-failure-lint.yml` | Findet stille Fehlschläge in Workflows | keine |
| `validate-workflows.yml` | Prüft Workflow-Syntax | keine |

Pflicht-Eingaben ohne Vorgabewert gibt es in genau drei Vorlagen:

| Vorlage | Pflicht |
|---|---|
| `_ci-odoo.yml` | `modules_to_test` |
| `_deploy-unified.yml` | `app_name`, `app_path` |
| `_deploy-hetzner.yml` | `app_name`, `deploy_path`, `health_url` |

Alle übrigen Eingaben haben Vorgabewerte. Die vollständige Liste steht im
`workflow_call`-Block der jeweiligen Datei.

### 4. Aktuell und bleiben

Der neueste Tag ist `v1.1.17`. Updates laufen über Dependabot; dafür braucht das
aufrufende Repo eine `.github/dependabot.yml` mit `package-ecosystem:
"github-actions"`.

### 5. Zwei Einschränkungen, die keine Vorgabe umgeht

- **Berechtigungen** kann ein Reusable-Workflow nur einschränken, nie erweitern.
  Was der Aufrufer nicht hat, bekommt die Vorlage auch nicht.
- **`_deploy-unified.yml`** erwartet eine Health-Check-Adresse, die auf `/healthz/`
  endet (ADR-022). Eine andere Adresse lässt den Lauf grün aussehen, ohne den
  Dienst geprüft zu haben.
