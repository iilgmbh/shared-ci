"""Tests fuer tools/check_haus_literale.py — Ratsche gegen Haus-Literale.

Anlass: meiki-lra/meiki-hub#582 Schritt 7. Jeder Test baut ein kleines Git-Repo,
weil der Pruefer nur verfolgte Dateien liest.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_haus_literale as hl  # noqa: E402

KONFIG = """\
format: haus-literale/1
muster:
  - name: ort
    regex: '(?i)beispielstadt'
    beispiel: 'ORT = "Beispielstadt"'
dateien: ['**/*.py']
ausnahmen: ['**/fixtures/**']
bestand_datei: .github/haus-literale-bestand.yml
"""


def _repo(tmp_path: Path, dateien: dict[str, str], konfig: str = KONFIG) -> Path:
    alle = {".github/haus-literale.yml": konfig, **dateien}
    for rel, inhalt in alle.items():
        pfad = tmp_path / rel
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(inhalt, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


def _lauf(wurzel: Path, *extra: str) -> int:
    return hl.main(["--wurzel", str(wurzel), *extra])


def _mit_bestand(wurzel: Path) -> Path:
    assert _lauf(wurzel, "--bestand-schreiben") == 0
    subprocess.run(["git", "-C", str(wurzel), "add", "-A"], check=True)
    return wurzel


def test_should_pass_when_hits_equal_bestand(tmp_path: Path) -> None:
    wurzel = _mit_bestand(_repo(tmp_path, {"app/a.py": 'ORT = "Beispielstadt"\n'}))
    assert _lauf(wurzel) == 0


def test_should_fail_on_new_literal_in_new_file(tmp_path: Path, capsys) -> None:
    wurzel = _mit_bestand(_repo(tmp_path, {"app/a.py": 'ORT = "Beispielstadt"\n'}))
    (wurzel / "app/b.py").write_text('X = "beispielstadt"\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(wurzel), "add", "-A"], check=True)
    assert _lauf(wurzel) == 1
    assert "NEU: app/b.py: 1 Haus-Literale, Bestand 0" in capsys.readouterr().out


def test_should_fail_when_existing_file_gains_a_literal(tmp_path: Path) -> None:
    wurzel = _mit_bestand(_repo(tmp_path, {"app/a.py": 'ORT = "Beispielstadt"\n'}))
    (wurzel / "app/a.py").write_text('ORT = "Beispielstadt"\nB = "Beispielstadt"\n')
    assert _lauf(wurzel) == 1


def test_should_fail_when_bestand_is_above_actual_hits(tmp_path: Path, capsys) -> None:
    wurzel = _mit_bestand(_repo(tmp_path, {"app/a.py": 'ORT = "Beispielstadt"\n'}))
    (wurzel / "app/a.py").write_text("ORT = lade_ort()\n")
    assert _lauf(wurzel) == 1
    assert "BESTAND SENKEN: app/a.py: 0 statt 1" in capsys.readouterr().out


def test_should_count_root_level_files_for_double_star_glob(tmp_path: Path) -> None:
    wurzel = _mit_bestand(_repo(tmp_path, {"top.py": 'ORT = "Beispielstadt"\n'}))
    bestand = hl.lade_bestand(wurzel / ".github/haus-literale-bestand.yml")
    assert bestand == {"top.py": 1}


def test_should_ignore_ausnahmen_untracked_and_own_files(tmp_path: Path) -> None:
    wurzel = _mit_bestand(
        _repo(
            tmp_path,
            {"app/a.py": "X = 1\n", "tests/fixtures/f.py": 'ORT = "Beispielstadt"\n'},
            konfig=KONFIG.replace("['**/*.py']", "['**/*.py', '**/*.yml']"),
        )
    )
    (wurzel / "app/frei.py").write_text('ORT = "Beispielstadt"\n')  # nicht verfolgt
    assert hl.lade_bestand(wurzel / ".github/haus-literale-bestand.yml") == {}
    assert _lauf(wurzel) == 0


def test_should_ignore_comments_and_docstrings_but_count_strings(tmp_path: Path) -> None:
    code = (
        '"""Modul fuer Beispielstadt."""\n'
        "# Beispielstadt im Kommentar\n"
        "def f():\n"
        '    """Docstring Beispielstadt."""\n'
        '    return f"Amt {1} Beispielstadt"\n'
    )
    skript = "# Beispielstadt\necho Beispielstadt\n"
    konfig = KONFIG.replace("['**/*.py']", "['**/*.py', '**/*.sh']")
    wurzel = _mit_bestand(_repo(tmp_path, {"a.py": code, "b.sh": skript}, konfig=konfig))
    bestand = hl.lade_bestand(wurzel / ".github/haus-literale-bestand.yml")
    assert bestand == {"a.py": 1, "b.sh": 1}


def test_should_scan_unparsable_python_line_by_line(tmp_path: Path) -> None:
    wurzel = _mit_bestand(_repo(tmp_path, {"kaputt.py": 'def (:\nX = "Beispielstadt"\n'}))
    assert hl.lade_bestand(wurzel / ".github/haus-literale-bestand.yml") == {"kaputt.py": 1}


def test_should_reject_wrong_format(tmp_path: Path) -> None:
    wurzel = _repo(tmp_path, {"a.py": "X = 1\n"}, konfig=KONFIG.replace("/1", "/9", 1))
    assert _lauf(wurzel, "--bestand-schreiben") == 2


def test_should_reject_muster_that_misses_its_own_beispiel(tmp_path: Path) -> None:
    konfig = KONFIG.replace("beispiel: 'ORT = \"Beispielstadt\"'", "beispiel: 'ORT = \"Anderswo\"'")
    wurzel = _repo(tmp_path, {"a.py": "X = 1\n"}, konfig=konfig)
    assert _lauf(wurzel, "--bestand-schreiben") == 2


@pytest.mark.parametrize("feld", ["name", "regex", "beispiel"])
def test_should_reject_muster_with_missing_field(tmp_path: Path, feld: str) -> None:
    zeilen = [z for z in KONFIG.splitlines() if not z.strip().startswith(f"{feld}:")]
    if feld == "name":
        zeilen = [z.replace("    regex:", "  - regex:") for z in zeilen]
    wurzel = _repo(tmp_path, {"a.py": "X = 1\n"}, konfig="\n".join(zeilen) + "\n")
    assert _lauf(wurzel, "--bestand-schreiben") == 2


def test_should_reject_run_that_checks_no_file(tmp_path: Path, capsys) -> None:
    wurzel = _repo(tmp_path, {"a.sh": "echo hallo\n"})
    assert _lauf(wurzel, "--bestand-schreiben") == 2
    assert "keine Datei geprüft" in capsys.readouterr().err


def test_should_reject_missing_bestand(tmp_path: Path) -> None:
    wurzel = _repo(tmp_path, {"a.py": "X = 1\n"})
    assert _lauf(wurzel) == 2


def test_should_reject_bestand_with_wrong_format(tmp_path: Path) -> None:
    wurzel = _repo(
        tmp_path,
        {"a.py": "X = 1\n", ".github/haus-literale-bestand.yml": "format: x\ntreffer: {}\n"},
    )
    assert _lauf(wurzel) == 2


def test_should_emit_github_annotation_with_line(tmp_path: Path, capsys, monkeypatch) -> None:
    wurzel = _mit_bestand(_repo(tmp_path, {"app/a.py": "X = 1\n"}))
    (wurzel / "app/a.py").write_text('X = 1\nORT = "Beispielstadt"\n')
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert _lauf(wurzel) == 1
    assert "::error file=app/a.py,line=2::Haus-Literal [ort]" in capsys.readouterr().out
