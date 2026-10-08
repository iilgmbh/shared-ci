#!/usr/bin/env python3
"""Ratsche gegen Haus-Literale im Code (meiki-lra/meiki-hub#582, Schritt 7).

Anlass: Fach-, Haus-, Test- und Demodaten gehören in Tabellen oder Datendateien,
nie in den Code (Owner-Regel 2026-10-07). Die Korrektur-PRs haben Literale
herausgezogen; ohne Gate kehren sie still zurück und „hardcoding-frei" bleibt
eine Behauptung.

Der Prüfer ist fachneutral. Was ein Haus-Literal ist, steht in einer Datei im
aufrufenden Repo (Format ``haus-literale/1``):

    format: haus-literale/1
    muster:
      - name: landkreis
        regex: '(?i)beispielstadt'
        beispiel: 'ORT = "Beispielstadt"'   # Probe: das Muster muss hier treffen
    dateien: ['**/*.py', '**/*.sh']       # geprüfte Dateien (nur von Git verfolgte)
    ausnahmen: ['**/fixtures/**']          # bewusst ausgenommen
    bestand_datei: .github/haus-literale-bestand.yml

Gezählt werden Zeichenketten im Code; Kommentare und Docstrings dürfen
Beispiele nennen (siehe ``zaehle``).

Der Bestand hält die Treffer je Datei fest, die es beim Einführen schon gab.
Geprüft wird als Ratsche:

- mehr Treffer als im Bestand (auch: neue Datei mit Treffer) ⇒ rot
- weniger Treffer als im Bestand ⇒ rot mit der Bitte, den Bestand zu senken —
  so kann ein abgebauter Treffer nicht still zurückkehren.

Gegen das Blindwerden (Memory „lint-imports lief nie"): jedes Muster muss auf
sein eigenes Beispiel treffen, und ein Lauf über null Dateien ist ein Fehler.

``--bestand-schreiben`` schreibt den heutigen Stand in die Bestandsdatei.

stdlib + PyYAML. Exit 0 = gleich Bestand, 1 = Abweichung, 2 = Konfigurations-
oder Laufzeitfehler.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - Umgebungsfrage, kein Logikpfad
    print("FEHLER: PyYAML fehlt (pip install pyyaml)", file=sys.stderr)
    raise SystemExit(2) from None

FORMAT = "haus-literale/1"
BESTAND_FORMAT = "haus-literale-bestand/1"
STANDARD_KONFIG = ".github/haus-literale.yml"
#: So viele Fundstellen je Datei werden ausgegeben; der Rest steht als Zahl da.
MAX_ZEILEN_JE_DATEI = 10
#: Zeilen, die so beginnen, gelten außerhalb von Python als Kommentar.
KOMMENTAR_ANFAENGE = ("#", "//", "/*", "*", "<!--", "{#")


class KonfigFehler(Exception):
    """Konfiguration oder Umgebung erlaubt keinen aussagekräftigen Lauf."""


@dataclass(frozen=True)
class Muster:
    name: str
    regex: re.Pattern[str]


@dataclass(frozen=True)
class Konfig:
    muster: tuple[Muster, ...]
    dateien: tuple[str, ...]
    ausnahmen: tuple[str, ...]
    bestand_datei: str


def _liste(roh: dict, feld: str, pflicht: bool) -> tuple[str, ...]:
    wert = roh.get(feld, [])
    if not isinstance(wert, list) or not all(isinstance(w, str) and w for w in wert):
        raise KonfigFehler(f"'{feld}' muss eine Liste nicht leerer Texte sein")
    if pflicht and not wert:
        raise KonfigFehler(f"'{feld}' ist leer")
    return tuple(wert)


def lade_konfig(pfad: Path) -> Konfig:
    try:
        roh = yaml.safe_load(pfad.read_text(encoding="utf-8"))
    except OSError as exc:
        raise KonfigFehler(f"Konfiguration nicht lesbar: {pfad} ({exc})") from exc
    except yaml.YAMLError as exc:
        raise KonfigFehler(f"Konfiguration ist kein YAML: {pfad}") from exc
    if not isinstance(roh, dict):
        raise KonfigFehler("Konfiguration muss ein Mapping sein")
    if roh.get("format") != FORMAT:
        raise KonfigFehler(f"format muss '{FORMAT}' sein, ist {roh.get('format')!r}")

    roh_muster = roh.get("muster")
    if not isinstance(roh_muster, list) or not roh_muster:
        raise KonfigFehler("'muster' muss eine nicht leere Liste sein")
    muster: list[Muster] = []
    for i, eintrag in enumerate(roh_muster):
        if not isinstance(eintrag, dict):
            raise KonfigFehler(f"muster[{i}] muss ein Mapping sein")
        name, regex, beispiel = (eintrag.get(k) for k in ("name", "regex", "beispiel"))
        for feld, wert in (("name", name), ("regex", regex), ("beispiel", beispiel)):
            if not isinstance(wert, str) or not wert.strip():
                raise KonfigFehler(f"muster[{i}].{feld} fehlt oder ist leer")
        try:
            kompiliert = re.compile(regex)
        except re.error as exc:
            raise KonfigFehler(f"muster '{name}': Regex ungültig ({exc})") from exc
        # Positivkontrolle je Muster: ein Muster, das sein Beispiel nicht findet,
        # findet auch im Code nichts und ließe das Gate still grün.
        if not kompiliert.search(beispiel):
            raise KonfigFehler(f"muster '{name}' trifft sein eigenes Beispiel nicht")
        muster.append(Muster(name, kompiliert))

    bestand_datei = roh.get("bestand_datei")
    if not isinstance(bestand_datei, str) or not bestand_datei.strip():
        raise KonfigFehler("'bestand_datei' fehlt")
    return Konfig(
        muster=tuple(muster),
        dateien=_liste(roh, "dateien", pflicht=True),
        ausnahmen=_liste(roh, "ausnahmen", pflicht=False),
        bestand_datei=bestand_datei,
    )


def lade_bestand(pfad: Path) -> dict[str, int]:
    if not pfad.is_file():
        raise KonfigFehler(f"Bestandsdatei fehlt: {pfad} (anlegen mit --bestand-schreiben)")
    try:
        roh = yaml.safe_load(pfad.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise KonfigFehler(f"Bestandsdatei ist kein YAML: {pfad}") from exc
    if not isinstance(roh, dict) or roh.get("format") != BESTAND_FORMAT:
        raise KonfigFehler(f"Bestandsdatei braucht format '{BESTAND_FORMAT}'")
    treffer = roh.get("treffer") or {}
    if not isinstance(treffer, dict) or not all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v > 0
        for k, v in treffer.items()
    ):
        raise KonfigFehler("'treffer' muss Datei → positive Ganzzahl sein")
    return dict(treffer)


def _passt(pfad: str, globs: tuple[str, ...]) -> bool:
    # '**/*.py' soll auch die Datei im Wurzelverzeichnis treffen.
    return any(
        fnmatch.fnmatchcase(pfad, g) or (g.startswith("**/") and fnmatch.fnmatchcase(pfad, g[3:]))
        for g in globs
    )


def verfolgte_dateien(wurzel: Path) -> list[str]:
    try:
        aus = subprocess.run(
            ["git", "-C", str(wurzel), "ls-files", "-z"],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise KonfigFehler(f"git ls-files in {wurzel} gescheitert ({exc})") from exc
    return sorted(p for p in aus.decode("utf-8").split("\0") if p)


def _docstrings(baum: ast.AST) -> set[int]:
    ids: set[int] = set()
    for knoten in ast.walk(baum):
        if isinstance(
            knoten, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and knoten.body:
            erste = knoten.body[0]
            if isinstance(erste, ast.Expr) and isinstance(erste.value, ast.Constant):
                ids.add(id(erste.value))
    return ids


def _python_texte(text: str) -> list[tuple[int, str]] | None:
    """Zeichenketten-Konstanten ohne Docstrings; ``None`` bei Syntaxfehler."""
    try:
        baum = ast.parse(text)
    except SyntaxError:
        return None
    docs = _docstrings(baum)
    return [
        (k.lineno, k.value)
        for k in ast.walk(baum)
        if isinstance(k, ast.Constant) and isinstance(k.value, str) and id(k) not in docs
    ]


def _zeilen_ohne_kommentar(text: str) -> list[tuple[int, str]]:
    return [
        (nr, zeile)
        for nr, zeile in enumerate(text.splitlines(), start=1)
        if not zeile.lstrip().startswith(KOMMENTAR_ANFAENGE)
    ]


def zaehle(
    wurzel: Path, konfig: Konfig, konfig_datei: str
) -> tuple[int, dict[str, list[tuple[int, str, str]]]]:
    """(Anzahl geprüfter Dateien, {datei: [(zeile, muster, text), …]}).

    Kommentare und Docstrings dürfen Beispiele nennen, wie in den Gates, die es
    schon gibt (frist-hub G1, post-hub test_kein_hausmerkmal). In Python zählen
    deshalb nur Zeichenketten außerhalb von Docstrings, sonst nur Zeilen, die
    kein Kommentar sind.
    """
    geprueft = 0
    funde: dict[str, list[tuple[int, str, str]]] = {}
    # Konfiguration und Bestand nennen die Literale selbst — sie sind Daten.
    eigene = {Path(konfig_datei).as_posix(), Path(konfig.bestand_datei).as_posix()}
    for rel in verfolgte_dateien(wurzel):
        if not _passt(rel, konfig.dateien) or _passt(rel, konfig.ausnahmen) or rel in eigene:
            continue
        try:
            text = (wurzel / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # z. B. verfolgter Symlink ins Leere
        geprueft += 1
        stellen = _python_texte(text) if rel.endswith(".py") else None
        if stellen is None:  # kein Python oder nicht parsebar: zeilenweise
            stellen = _zeilen_ohne_kommentar(text)
        for nr, inhalt in stellen:
            for m in konfig.muster:
                for _ in m.regex.finditer(inhalt):
                    funde.setdefault(rel, []).append((nr, m.name, inhalt.strip()))
    return geprueft, funde


def schreibe_bestand(pfad: Path, ist: dict[str, int]) -> None:
    kopf = (
        "# Bestand der Haus-Literale beim Einführen des Gates (tools/check_haus_literale.py).\n"
        "# Nur senken: ein abgebauter Treffer soll nicht zurückkehren. Erhöhen nur mit Grund im PR.\n"
    )
    inhalt = {"format": BESTAND_FORMAT, "treffer": dict(sorted(ist.items()))}
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(
        kopf + yaml.safe_dump(inhalt, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def vergleiche(
    ist_funde: dict[str, list[tuple[int, str, str]]], bestand: dict[str, int]
) -> list[str]:
    meldungen: list[str] = []
    annotieren = os.environ.get("GITHUB_ACTIONS") == "true"
    for datei in sorted(set(ist_funde) | set(bestand)):
        funde = ist_funde.get(datei, [])
        ist, soll = len(funde), bestand.get(datei, 0)
        if ist > soll:
            meldungen.append(f"NEU: {datei}: {ist} Haus-Literale, Bestand {soll}")
            for nr, name, text in funde[:MAX_ZEILEN_JE_DATEI]:
                meldungen.append(f"    {datei}:{nr} [{name}] {text[:120]}")
                if annotieren:
                    print(f"::error file={datei},line={nr}::Haus-Literal [{name}] — "
                          "Wert gehört in Tabelle oder Datendatei")
            if ist > MAX_ZEILEN_JE_DATEI:
                meldungen.append(f"    … und {ist - MAX_ZEILEN_JE_DATEI} weitere")
        elif ist < soll:
            meldungen.append(
                f"BESTAND SENKEN: {datei}: {ist} statt {soll} — Bestandsdatei nachziehen"
            )
    return meldungen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wurzel", default=".", help="Repo-Wurzel (Standard: .)")
    parser.add_argument("--konfig", default=STANDARD_KONFIG, help="relativ zur Wurzel")
    parser.add_argument(
        "--bestand-schreiben", action="store_true", help="heutigen Stand als Bestand speichern"
    )
    args = parser.parse_args(argv)
    wurzel = Path(args.wurzel).resolve()

    try:
        konfig = lade_konfig(wurzel / args.konfig)
        geprueft, funde = zaehle(wurzel, konfig, args.konfig)
        if geprueft == 0:
            raise KonfigFehler("keine Datei geprüft — 'dateien' trifft nichts im Repo")
        ist = {datei: len(f) for datei, f in funde.items()}
        if args.bestand_schreiben:
            schreibe_bestand(wurzel / konfig.bestand_datei, ist)
            print(f"haus-literale: Bestand geschrieben, {sum(ist.values())} Treffer "
                  f"in {len(ist)} Dateien")
            return 0
        bestand = lade_bestand(wurzel / konfig.bestand_datei)
    except KonfigFehler as exc:
        print(f"FEHLER haus-literale: {exc}", file=sys.stderr)
        return 2

    meldungen = vergleiche(funde, bestand)
    print(
        f"haus-literale: {geprueft} Dateien geprüft, {len(konfig.muster)} Muster "
        f"(je Muster Probe bestanden), {sum(ist.values())} Treffer in {len(ist)} Dateien, "
        f"Bestand {sum(bestand.values())}"
    )
    for zeile in meldungen:
        print(zeile)
    return 1 if meldungen else 0


if __name__ == "__main__":
    raise SystemExit(main())
