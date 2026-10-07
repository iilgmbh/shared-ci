"""Tests fuer tools/deploy_config_lint.py — Auto-Prod-Default und Ausnahme-Marker.

Anlass: iilgmbh/shared-ci#104 — das Mandat achimdehnert/platform#3804 (RA) verlangt
push->main = production fuer risk-hub; der Lint verbot genau das. Der Marker
oeffnet die Ausnahme nur mit Health-Gate und Rollback-Pfad im selben Workflow.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deploy_config_lint as lint  # noqa: E402

FALLBACK = "      target_environment: ${{ inputs.target_environment || 'production' }}\n"
MARKER = "# deploy-config-lint: auto-prod ok — Mandat achimdehnert/platform#3804, Gates: Health + Auto-Rollback\n"
HEALTH = "      health_check_url: https://example.test/healthz/\n"
REUSABLE = "    uses: iilgmbh/shared-ci/.github/workflows/_deploy-unified.yml@v1.1.24\n"
EIGENER_ROLLBACK = "      - name: Rollback auf vorheriges Image\n        run: ./deploy.sh rollback\n"
DISPATCH_PROD_DEFAULT = (
    "      target_environment:\n        description: Ziel\n        default: 'production'\n"
)


def test_should_flag_fallback_without_marker() -> None:
    out = lint.lint_text("deploy.yml", FALLBACK + HEALTH + REUSABLE)
    assert len(out) == 1
    assert "faellt auf production zurueck" in out[0]
    assert "Marker" not in out[0]


def test_should_accept_fallback_with_marker_health_and_reusable() -> None:
    assert lint.lint_text("deploy.yml", MARKER + FALLBACK + HEALTH + REUSABLE) == []


def test_should_accept_fallback_with_marker_health_and_own_rollback_step() -> None:
    assert lint.lint_text("deploy.yml", MARKER + FALLBACK + HEALTH + EIGENER_ROLLBACK) == []


def test_should_flag_marker_without_health_check_url() -> None:
    out = lint.lint_text("deploy.yml", MARKER + FALLBACK + REUSABLE)
    assert len(out) == 1
    assert "ohne Beleg: health_check_url" in out[0]


def test_should_flag_marker_without_rollback_path() -> None:
    out = lint.lint_text("deploy.yml", MARKER + FALLBACK + HEALTH)
    assert len(out) == 1
    assert "Rollback-Pfad" in out[0]


def test_should_ignore_health_and_rollback_that_live_only_in_comments() -> None:
    kommentare = "# health_check_url: https://x/healthz/\n# Auto-Rollback in deploy.sh\n"
    out = lint.lint_text("deploy.yml", MARKER + FALLBACK + kommentare)
    assert len(out) == 1
    assert "health_check_url" in out[0]
    assert "Rollback-Pfad" in out[0]


def test_should_reject_marker_without_reason() -> None:
    ohne_grund = "# deploy-config-lint: auto-prod ok —\n"
    out = lint.lint_text("deploy.yml", ohne_grund + FALLBACK + HEALTH + REUSABLE)
    assert len(out) == 1
    assert "Marker" not in out[0]


def test_should_still_flag_dispatch_default_production_despite_marker() -> None:
    out = lint.lint_text("deploy.yml", MARKER + DISPATCH_PROD_DEFAULT + HEALTH + REUSABLE)
    assert len(out) == 1
    assert "workflow_dispatch" in out[0]


def test_should_pass_clean_staging_default() -> None:
    staging = "      target_environment: ${{ inputs.target_environment || 'staging' }}\n"
    assert lint.lint_text("deploy.yml", staging) == []
