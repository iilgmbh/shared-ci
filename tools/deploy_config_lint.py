#!/usr/bin/env python3
"""deploy_config_lint.py — Org-Gate gegen Auto-Prod-Deploy-Drift.

Prueft die Deploy-Workflows eines Repos darauf, dass push->main NICHT auf
'production' defaultet. Org-Standard (platform/_deploy-unified.yml):
push->main = staging; Prod nur via Tag v* oder bewusster workflow_dispatch-Wahl.

Hintergrund: risk-hub deployte ~20 Tage unbemerkt push->main direkt auf Prod,
weil deploy.yml `target_environment` auf 'production' defaultete (session-retro
2026-06-04, run wf_313a3b58). Dieses Lint faengt die Drift-Klasse org-weit am
PR-Gate — statt N-ter Memory-Eintrag.

Ausnahme (iilgmbh/shared-ci#104, Mandat achimdehnert/platform#3804 Zeile RA): ein Repo
darf push->main bewusst auf production legen, wenn der Workflow eine Markerzeile traegt
    # deploy-config-lint: auto-prod ok — <Grund mit Mandats-Link>
UND im selben Workflow der Health-/Rollback-Pfad steht: `health_check_url:` gesetzt und
entweder die Reusable `_deploy-unified.yml` (Health-Gate + Auto-Rollback in deploy.sh)
aufgerufen oder ein eigener Rollback-Schritt ausserhalb von Kommentaren vorhanden. Fehlt
eines davon, bleibt der Marker wirkungslos und der Lint rot. Der Marker deckt nur
Anti-Pattern 1; ein workflow_dispatch-Default 'production' bleibt immer ein Befund.

Usage:
    python3 deploy_config_lint.py [<workflows-dir>]   # default .github/workflows
Exit 1 wenn ein Auto-Prod-Default-Anti-Pattern gefunden wird, sonst 0.
"""

from __future__ import annotations

import pathlib
import re
import sys

# Anti-Pattern 1 (Haupt-Drift-Ursache): push-Fallback auf production, z.B.
#   target_environment: ${{ inputs.target_environment || 'production' }}
_FALLBACK = re.compile(r"""target_environment\s*:.*\|\|\s*['"]production['"]""")

# Ausnahme-Marker: eigene Kommentarzeile, Grund ist Pflicht (mindestens ein Zeichen
# nach dem Gedankenstrich/Bindestrich), damit „auto-prod ok" nie grundlos dasteht.
_MARKER = re.compile(r"""^[ \t]*#[ \t]*deploy-config-lint:[ \t]*auto-prod ok[ \t]*[—-]+[ \t]*\S""", re.M)

# Belege, die der Marker braucht — nur ausserhalb von Kommentarzeilen gezaehlt,
# sonst reicht eine Prosa-Erwaehnung („Auto-Rollback in deploy.sh") als Nachweis.
_HEALTH = re.compile(r"""^[ \t]*health_check_url\s*:""", re.M)
_ROLLBACK_PFAD = re.compile(r"""_deploy-unified\.ya?ml@|rollback""", re.I)


def _ohne_kommentare(text: str) -> str:
    return "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))


def marker_greift(text: str) -> tuple[bool, list[str]]:
    """Traegt der Workflow den Ausnahme-Marker UND die geforderten Belege?

    Rueckgabe: (greift, fehlende Belege). Ohne Marker: (False, []).
    """
    if not _MARKER.search(text):
        return False, []
    code = _ohne_kommentare(text)
    fehlt: list[str] = []
    if not _HEALTH.search(code):
        fehlt.append("health_check_url")
    if not _ROLLBACK_PFAD.search(code):
        fehlt.append("Rollback-Pfad (_deploy-unified.yml oder eigener Rollback-Schritt)")
    return not fehlt, fehlt

# Anti-Pattern 2: workflow_dispatch-Input-Default 'production' im
# target_environment-Block (Mehrzeilen-Block bis zum naechsten default:).
_INPUT_DEFAULT = re.compile(
    r"""target_environment\s*:\s*\n(?:[ \t]+\S.*\n)*?[ \t]+default\s*:\s*['"]production['"]""",
)


def lint_text(name: str, text: str) -> list[str]:
    """Findet Auto-Prod-Default-Anti-Pattern in einem Workflow-Text."""
    out: list[str] = []
    greift, fehlt = marker_greift(text)
    for m in _FALLBACK.finditer(text):
        if greift:
            continue
        hinweis = (
            f" — Marker 'auto-prod ok' vorhanden, aber ohne Beleg: {', '.join(fehlt)}"
            if fehlt
            else ""
        )
        out.append(
            f"{name}: push->branch faellt auf production zurueck (Default sollte 'staging'): "
            f"{m.group(0).strip()}{hinweis}"
        )
    if _INPUT_DEFAULT.search(text):
        out.append(
            f"{name}: workflow_dispatch target_environment-Default = 'production' (sollte 'staging')"
        )
    return out


def lint_dir(d: pathlib.Path) -> list[str]:
    out: list[str] = []
    for f in sorted([*d.glob("*.yml"), *d.glob("*.yaml")]):
        out.extend(lint_text(str(f), f.read_text(encoding="utf-8", errors="ignore")))
    return out


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    d = pathlib.Path(argv[1] if len(argv) > 1 else ".github/workflows")
    if not d.is_dir():
        print(f"✅ Deploy-Config-Lint: kein Verzeichnis {d} — nichts zu pruefen.")
        return 0
    offenders = lint_dir(d)
    if offenders:
        print("❌ Deploy-Config-Lint: Auto-Prod-Default-Anti-Pattern gefunden:")
        for o in offenders:
            print("  -", o)
        print(
            "\nFix: target_environment-Default auf 'staging'. Prod nur via Tag v* "
            "oder bewusster workflow_dispatch-Wahl (Org-Standard _deploy-unified.yml).\n"
            "Bewusste Ausnahme (Mandat): Markerzeile "
            "'# deploy-config-lint: auto-prod ok — <Grund>' plus health_check_url und "
            "Rollback-Pfad im selben Workflow (iilgmbh/shared-ci#104)."
        )
        return 1
    print("✅ Deploy-Config-Lint: kein Auto-Prod-Default.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
