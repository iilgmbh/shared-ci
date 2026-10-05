#!/usr/bin/env python3
"""Dependabot-Updates fremder Actions mergen und den naechsten Tag setzen.

**Warum es das gibt (platform#3775, Owner-Go 2026-10-05 "weg 2 ist ok go"):**
Jeder Versions-Bump einer Action liess die Drift-Pruefung in jedem aufrufenden
Repo einen Error melden, bis jemand den Bump hier von Hand gemergt und getaggt
hatte. Die Bump-PRs selbst schreibt Dependabot laengst (#91, #92 lagen seit
2026-09-24 gruen und ungemergt). Uebrig war ein reiner Handgriff — der faellt
hiermit weg, aber **nur** fuer Aenderungen, bei denen es nichts zu entscheiden
gibt.

Was dieses Werkzeug anfasst, ist bewusst eng:

* **Merge** nur, wenn der PR von Dependabot stammt, kein Entwurf ist, alle
  Checks gruen sind (mindestens einer) und jede geaenderte Zeile eine
  `uses:`-Zeile einer **fremden** Action ist, deren Hauptversion gleich bleibt.
  Eigene Actions (`achimdehnert/`, `iilgmbh/`, ...) bleiben Mensch: ein Bump
  dort holt ungeprueften Code aus einem anderen Repo herein.
* **Tag** nur, wenn alles zwischen dem neuesten Tag und `main` dieselbe Pruefung
  besteht. Liegt dort auch nur eine andere Zeile, bleibt das Release beim
  Menschen — sonst wuerde eine von Hand gemergte Aenderung still mitveroeffentlicht.

Jeder uebersprungene PR nennt seinen Grund; ein stummer Skip ist dieselbe Klasse
wie ein stummer Melder (siehe `bot_review_kandidaten.py`). Unklar ist nie ein Ja:
API-Fehler beenden den Lauf mit Exit 1, ohne zu mergen oder zu taggen.

Aufruf:

    python3 tools/auto_release.py --repo iilgmbh/shared-ci [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bot_review_kandidaten import nicht_gruene_checks  # noqa: E402

DEPENDABOT_LOGINS = ("app/dependabot", "dependabot[bot]")

#: Actions aus diesen Orgs werden nie automatisch nachgezogen — ihr neuer Stand
#: ist Code aus einem eigenen Repo, kein fremdes Release.
EIGENE_ORGS = ("achimdehnert/", "iilgmbh/", "meiki-lra/", "ttz-lif/", "iilsandbox/")

#: Nur Dateien unter diesem Praefix darf ein Auto-Merge aendern.
ERLAUBTER_PFAD = ".github/"

USES = re.compile(
    r"^(?P<vorspann>\s*-?\s*uses:\s*)"
    r"(?P<action>[\w.-]+/[\w./-]+)@(?P<ref>[\w.-]+)"
    r"(?:\s*#\s*(?P<version>v?\d[\w.-]*))?\s*$"
)
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def hauptversion(version: str | None) -> str | None:
    if not version:
        return None
    return version.lstrip("v").split(".")[0] or None


def versionszeilen_pruefen(patch: str | None) -> list[str]:
    """Gruende, warum ein Patch KEIN reiner Versions-Bump ist (leer = ok).

    Erwartet das `patch`-Feld der GitHub-API (ohne Datei-Kopf). Entfernte und
    hinzugefuegte Zeilen werden in Reihenfolge gepaart; jedes Paar muss dieselbe
    fremde Action mit gleicher Hauptversion auf einen anderen Ref setzen.
    """
    if patch is None:
        return ["kein Patch geliefert (Datei zu gross oder binaer)"]
    weg: list[str] = []
    neu: list[str] = []
    for zeile in patch.splitlines():
        if zeile.startswith("@@") or zeile.startswith("\\"):
            continue
        if zeile.startswith("-"):
            weg.append(zeile[1:])
        elif zeile.startswith("+"):
            neu.append(zeile[1:])
    if not weg and not neu:
        return ["keine geaenderte Zeile"]
    if len(weg) != len(neu):
        return [f"{len(weg)} Zeilen entfernt, {len(neu)} hinzugefuegt"]
    gruende: list[str] = []
    for alt, jetzt in zip(weg, neu):
        a, b = USES.match(alt), USES.match(jetzt)
        if not a or not b:
            gruende.append(f"keine uses-Zeile: {jetzt.strip()[:80]}")
            continue
        action = b["action"]
        if a["action"] != action or a["vorspann"] != b["vorspann"]:
            gruende.append(f"andere Action oder Einrueckung: {action}")
        elif action.startswith(EIGENE_ORGS):
            gruende.append(f"eigene Action bleibt Mensch: {action}")
        elif a["ref"] == b["ref"]:
            gruende.append(f"Ref unveraendert: {action}")
        elif hauptversion(a["version"]) is None or hauptversion(b["version"]) is None:
            gruende.append(f"Version nicht lesbar: {action}")
        elif hauptversion(a["version"]) != hauptversion(b["version"]):
            gruende.append(f"Hauptversion wechselt: {action} {a['version']} -> {b['version']}")
    return gruende


def dateien_pruefen(dateien: list[dict]) -> list[str]:
    """Gruende ueber alle Dateien eines PR oder Vergleichs (leer = ok)."""
    if not dateien:
        return ["keine Datei geaendert"]
    gruende: list[str] = []
    for d in dateien:
        pfad = d.get("filename", "?")
        if not pfad.startswith(ERLAUBTER_PFAD):
            gruende.append(f"{pfad}: ausserhalb von {ERLAUBTER_PFAD}")
        elif d.get("status") != "modified":
            gruende.append(f"{pfad}: Status {d.get('status')}")
        else:
            gruende.extend(f"{pfad}: {g}" for g in versionszeilen_pruefen(d.get("patch")))
    return gruende


def pr_pruefen(pr: dict) -> list[str]:
    """Gruende aus den PR-Metadaten (`gh pr list --json`), leer = weiter pruefen."""
    gruende: list[str] = []
    login = (pr.get("author") or {}).get("login")
    if login not in DEPENDABOT_LOGINS:
        gruende.append(f"Autor {login} ist nicht Dependabot")
    if pr.get("isDraft"):
        gruende.append("Entwurf")
    if pr.get("mergeable") != "MERGEABLE":
        gruende.append(f"mergeable={pr.get('mergeable')}")
    checks = pr.get("statusCheckRollup") or []
    if not checks:
        gruende.append("kein einziger Check gelaufen")
    else:
        rot = nicht_gruene_checks(checks)
        if rot:
            gruende.append("nicht gruen: " + ", ".join(sorted(rot)))
    return gruende


def naechster_tag(tags: list[str]) -> tuple[str, str]:
    """(neuester vX.Y.Z, naechster Patch-Tag); Tags anderer Form zaehlen nicht."""
    versionen = sorted(
        (tuple(int(x) for x in m.groups()), t)
        for t in tags
        if (m := TAG.match(t))
    )
    if not versionen:
        raise ValueError("kein Tag der Form vX.Y.Z gefunden")
    (major, minor, patch), neuester = versionen[-1]
    return neuester, f"v{major}.{minor}.{patch + 1}"


# ── GitHub-Zugriff (duenne Schicht ueber gh) ────────────────────────────────


def gh(*args: str, eingabe: dict | None = None) -> object:
    ergebnis = subprocess.run(
        ["gh", *args],
        input=json.dumps(eingabe) if eingabe is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if ergebnis.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {ergebnis.stderr.strip()[:300]}")
    return json.loads(ergebnis.stdout) if ergebnis.stdout.strip() else None


def offene_dependabot_prs(repo: str) -> list[dict]:
    return gh(
        "pr", "list", "--repo", repo, "--state", "open", "--author", "app/dependabot",
        "--json", "number,title,author,isDraft,mergeable,headRefOid,statusCheckRollup",
    )


def pr_dateien(repo: str, nummer: int) -> list[dict]:
    seiten = gh("api", "--paginate", "--slurp", f"repos/{repo}/pulls/{nummer}/files")
    return [d for seite in seiten for d in seite]


def mergen(repo: str, pr: dict) -> None:
    # sha pinnt den gepruefte Kopf: kam nach der Pruefung ein Push, lehnt GitHub ab.
    gh(
        "api", "-X", "PUT", f"repos/{repo}/pulls/{pr['number']}/merge", "--input", "-",
        eingabe={"merge_method": "squash", "sha": pr["headRefOid"]},
    )


def alle_tags(repo: str) -> dict[str, str]:
    seiten = gh("api", "--paginate", "--slurp", f"repos/{repo}/tags")
    return {t["name"]: t["commit"]["sha"] for seite in seiten for t in seite}


def taggen(repo: str, tag: str, sha: str, nachricht: str) -> None:
    objekt = gh(
        "api", "-X", "POST", f"repos/{repo}/git/tags", "--input", "-",
        eingabe={"tag": tag, "message": nachricht, "object": sha, "type": "commit"},
    )
    gh(
        "api", "-X", "POST", f"repos/{repo}/git/refs", "--input", "-",
        eingabe={"ref": f"refs/tags/{tag}", "sha": objekt["sha"]},
    )


# ── Ablauf ──────────────────────────────────────────────────────────────────


def lauf(repo: str, trocken: bool) -> int:
    modus = "TROCKEN" if trocken else "SCHARF"
    print(f"auto_release {repo} [{modus}]")

    gemergt: list[int] = []
    fehlgeschlagen: list[int] = []
    for pr in offene_dependabot_prs(repo):
        nr = pr["number"]
        gruende = pr_pruefen(pr) or dateien_pruefen(pr_dateien(repo, nr))
        if gruende:
            print(f"  #{nr} bleibt liegen: " + "; ".join(dict.fromkeys(gruende)))
            continue
        if trocken:
            print(f"  #{nr} wuerde gemergt: {pr['title']}")
        else:
            try:
                mergen(repo, pr)
            except RuntimeError as fehler:
                # Ein abgelehnter Merge (Konflikt, Kopf bewegt) haelt die
                # uebrigen nicht auf; der Lauf endet trotzdem rot.
                print(f"::error::#{nr} nicht gemergt: {fehler}")
                fehlgeschlagen.append(nr)
                continue
            print(f"  #{nr} gemergt: {pr['title']}")
        gemergt.append(nr)

    main_sha = gh("api", f"repos/{repo}/commits/main", "--jq", "{sha: .sha}")["sha"]
    tags = alle_tags(repo)
    neuester, naechster = naechster_tag(list(tags))
    vergleich = gh("api", f"repos/{repo}/compare/{tags[neuester]}...{main_sha}")
    ende = 1 if fehlgeschlagen else 0
    if vergleich["status"] == "identical":
        hinweis = " (die Merges oben kommen erst im scharfen Lauf auf main)" if trocken and gemergt else ""
        print(f"  Release: main = {neuester}, nichts zu taggen{hinweis}")
        return ende
    if vergleich["status"] != "ahead":
        print(f"::warning::Release bleibt Mensch: main ist zu {neuester} '{vergleich['status']}'")
        return ende
    if vergleich["ahead_by"] >= 250 or len(vergleich.get("files", [])) >= 300:
        print(f"::warning::Release bleibt Mensch: Vergleich {neuester}...main zu gross")
        return ende
    gruende = dateien_pruefen(vergleich.get("files", []))
    if gruende:
        print(
            f"::notice::Release bleibt Mensch: {neuester}...main enthaelt mehr als "
            "Action-Versionen — " + "; ".join(gruende)
        )
        return ende
    nachricht = (
        f"{naechster}: Action-Versionen nachgezogen ({vergleich['ahead_by']} Commit(s)) "
        "— auto_release, platform#3775"
    )
    if trocken:
        print(f"  wuerde taggen: {naechster} auf {main_sha[:8]} — {nachricht}")
    else:
        taggen(repo, naechster, main_sha, nachricht)
        print(f"  getaggt: {naechster} auf {main_sha[:8]}")
    return ende


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        return lauf(args.repo, args.dry_run)
    except (RuntimeError, ValueError, KeyError) as fehler:
        print(f"::error::auto_release abgebrochen, nichts weiter getan: {fehler}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
