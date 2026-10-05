"""Tests der Auto-Release-Pruefung (platform#3775).

Jede Ausnahme, die das Werkzeug zulaesst, hat hier einen Gegentest: was NICHT
gemergt oder getaggt werden darf, steht neben dem, was durchgeht.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auto_release  # noqa: E402
from auto_release import (  # noqa: E402
    dateien_pruefen,
    naechster_tag,
    pr_pruefen,
    versionszeilen_pruefen,
)

ALT = "        uses: github/codeql-action/upload-sarif@cdf488f595d80d6e07e03d4674febd5ab45fa938 # v4.37.9"
NEU = "        uses: github/codeql-action/upload-sarif@1c5b675653bb5c22dbe9b12b556ec555138e09fd # v4.38.1"

#: Echter Patch aus shared-ci#91 (gekuerzt um den Kontext am Ende).
PATCH_91 = f"""@@ -125,7 +125,7 @@ jobs:

       - name: Upload Trivy results
         if: ${{{{ inputs.scan_image }}}}
-{ALT}
+{NEU}
         with:
           sarif_file: 'trivy-results.sarif'"""


def patch(alt: str, neu: str) -> str:
    return f"@@ -1,1 +1,1 @@\n-{alt}\n+{neu}"


def datei(patch_text: str | None, pfad: str = ".github/workflows/_build-docker.yml", status: str = "modified") -> dict:
    return {"filename": pfad, "status": status, "patch": patch_text}


def pr(**felder) -> dict:
    grund = {
        "number": 91,
        "author": {"login": "app/dependabot"},
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [{"name": "Validate Syntax", "conclusion": "SUCCESS"}],
    }
    grund.update(felder)
    return grund


# ── Versionszeilen ──────────────────────────────────────────────────────────


def test_should_accept_real_dependabot_patch():
    assert versionszeilen_pruefen(PATCH_91) == []


def test_should_reject_changed_non_uses_line():
    assert versionszeilen_pruefen(patch("  sarif_file: a", "  sarif_file: b"))


def test_should_reject_own_org_action():
    alt = "      uses: achimdehnert/platform/.github/actions/workflow-guards@92a7b75 # v1.0.0"
    neu = "      uses: achimdehnert/platform/.github/actions/workflow-guards@1173271 # v1.0.1"
    gruende = versionszeilen_pruefen(patch(alt, neu))
    assert gruende and "eigene Action" in gruende[0]


def test_should_reject_major_bump():
    neu = NEU.replace("# v4.38.1", "# v5.0.0")
    gruende = versionszeilen_pruefen(patch(ALT, neu))
    assert gruende and "Hauptversion" in gruende[0]


def test_should_reject_missing_version_comment():
    neu = NEU.split(" #")[0]
    assert versionszeilen_pruefen(patch(ALT, neu))


def test_should_reject_swapped_action():
    neu = NEU.replace("upload-sarif", "analyze")
    assert versionszeilen_pruefen(patch(ALT, neu))


def test_should_reject_added_line_without_removal():
    assert versionszeilen_pruefen(f"@@ -1,0 +1,1 @@\n+{NEU}")


def test_should_reject_missing_patch():
    assert versionszeilen_pruefen(None)


# ── Dateien ─────────────────────────────────────────────────────────────────


def test_should_accept_version_only_files():
    assert dateien_pruefen([datei(PATCH_91)]) == []


def test_should_reject_file_outside_github_dir():
    assert dateien_pruefen([datei(PATCH_91, pfad="tools/auto_release.py")])


def test_should_reject_added_file():
    assert dateien_pruefen([datei(PATCH_91, status="added")])


def test_should_reject_empty_file_list():
    assert dateien_pruefen([])


def test_should_reject_mix_of_bump_and_other_change():
    andere = datei(patch("  a: 1", "  a: 2"), pfad=".github/workflows/_deploy-unified.yml")
    assert dateien_pruefen([datei(PATCH_91), andere])


# ── PR-Metadaten ────────────────────────────────────────────────────────────


def test_should_accept_green_dependabot_pr():
    assert pr_pruefen(pr()) == []


def test_should_reject_other_author():
    assert pr_pruefen(pr(author={"login": "achimdehnert"}))


def test_should_reject_draft():
    assert pr_pruefen(pr(isDraft=True))


def test_should_reject_unknown_mergeable():
    assert pr_pruefen(pr(mergeable="UNKNOWN"))


def test_should_reject_pr_without_checks():
    gruende = pr_pruefen(pr(statusCheckRollup=[]))
    assert gruende and "kein einziger Check" in gruende[0]


def test_should_reject_pending_check():
    laeuft = {"name": "Validate Syntax", "conclusion": "", "status": "IN_PROGRESS"}
    assert pr_pruefen(pr(statusCheckRollup=[laeuft]))


def test_should_reject_red_check():
    rot = {"name": "Validate Syntax", "conclusion": "FAILURE"}
    assert pr_pruefen(pr(statusCheckRollup=[rot]))


# ── Tags ────────────────────────────────────────────────────────────────────


def test_should_sort_tags_numerically():
    assert naechster_tag(["v1.1.9", "v1.1.21", "v1.0.30", "v1"]) == ("v1.1.21", "v1.1.22")


def test_should_fail_without_semver_tag():
    with pytest.raises(ValueError):
        naechster_tag(["v1", "latest"])


# ── Ablauf mit gestubbtem GitHub ────────────────────────────────────────────


class GitHubStub:
    """Ersetzt `gh` und protokolliert jeden schreibenden Aufruf."""

    def __init__(self, prs, pr_files, vergleich):
        self.prs, self.pr_files, self.vergleich = prs, pr_files, vergleich
        self.schreibend: list[str] = []

    def __call__(self, *args, eingabe=None):
        pfad = next((a for a in args if a.startswith("repos/")), "")
        if args[:2] == ("pr", "list"):
            return self.prs
        if "-X" in args:
            self.schreibend.append(pfad)
            return {"sha": "tagobjekt"}
        if pfad.endswith("/files"):
            return [self.pr_files]
        if pfad.endswith("/commits/main"):
            return {"sha": "mainsha00"}
        if pfad.endswith("/tags"):
            return [[{"name": "v1.1.21", "commit": {"sha": "tagsha00"}}]]
        if "/compare/" in pfad:
            return self.vergleich
        raise AssertionError(f"unerwarteter Aufruf: {args}")


def lauf_mit(monkeypatch, stub, trocken=False) -> int:
    monkeypatch.setattr(auto_release, "gh", stub)
    return auto_release.lauf("iilgmbh/shared-ci", trocken)


def test_should_merge_and_tag_version_only_change(monkeypatch):
    stub = GitHubStub(
        [pr(headRefOid="kopf", title="bump")],
        [datei(PATCH_91)],
        {"status": "ahead", "ahead_by": 1, "files": [datei(PATCH_91)]},
    )
    assert lauf_mit(monkeypatch, stub) == 0
    assert stub.schreibend == [
        "repos/iilgmbh/shared-ci/pulls/91/merge",
        "repos/iilgmbh/shared-ci/git/tags",
        "repos/iilgmbh/shared-ci/git/refs",
    ]


def test_should_not_write_in_dry_run(monkeypatch):
    stub = GitHubStub(
        [pr(headRefOid="kopf", title="bump")],
        [datei(PATCH_91)],
        {"status": "ahead", "ahead_by": 1, "files": [datei(PATCH_91)]},
    )
    assert lauf_mit(monkeypatch, stub, trocken=True) == 0
    assert stub.schreibend == []


def test_should_not_tag_when_main_has_other_changes(monkeypatch):
    andere = datei(patch("  a: 1", "  a: 2"), pfad=".github/workflows/_deploy-unified.yml")
    stub = GitHubStub([], [], {"status": "ahead", "ahead_by": 2, "files": [datei(PATCH_91), andere]})
    assert lauf_mit(monkeypatch, stub) == 0
    assert stub.schreibend == []


def test_should_not_tag_diverged_main(monkeypatch):
    stub = GitHubStub([], [], {"status": "diverged", "ahead_by": 1, "files": [datei(PATCH_91)]})
    assert lauf_mit(monkeypatch, stub) == 0
    assert stub.schreibend == []


def test_should_not_tag_identical_main(monkeypatch):
    stub = GitHubStub([], [], {"status": "identical", "ahead_by": 0, "files": []})
    assert lauf_mit(monkeypatch, stub) == 0
    assert stub.schreibend == []


def test_should_end_red_when_merge_rejected(monkeypatch):
    stub = GitHubStub(
        [pr(headRefOid="kopf", title="bump")],
        [datei(PATCH_91)],
        {"status": "identical", "ahead_by": 0, "files": []},
    )

    def ablehnen(*args, eingabe=None):
        if "-X" in args:
            raise RuntimeError("405 Head branch was modified")
        return stub(*args, eingabe=eingabe)

    monkeypatch.setattr(auto_release, "gh", ablehnen)
    assert auto_release.lauf("iilgmbh/shared-ci", False) == 1
