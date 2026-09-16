"""Konsumenten-Erweiterung (platform#3268): AUCH_CLEAN + Pruefrage-Tabu."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot_review_kandidaten as k  # noqa: E402


def _pr(nr, **o):
    b = dict(
        number=nr,
        isDraft=False,
        author={"login": "achimdehnert"},
        mergeStateStatus="CLEAN",
        reviewDecision=None,
        reviews=[],
        files=[{"path": "src/x.py"}],
        statusCheckRollup=[
            {"name": "ci", "conclusion": "SUCCESS", "startedAt": "2026-09-16T10:00:00Z"}
        ],
    )
    b.update(o)
    return b


def test_should_skip_clean_pr_by_default(monkeypatch):
    monkeypatch.delenv("AUCH_CLEAN", raising=False)
    kand, _ = k.waehle_kandidaten([_pr(1)], "IIL-Lotse", "achimdehnert")
    assert kand == []


def test_should_approve_clean_pr_when_auch_clean(monkeypatch):
    monkeypatch.setenv("AUCH_CLEAN", "1")
    kand, _ = k.waehle_kandidaten([_pr(1)], "IIL-Lotse", "achimdehnert")
    assert kand == [1]


def test_should_keep_deploy_definition_and_migrations_human(monkeypatch):
    monkeypatch.setenv("AUCH_CLEAN", "1")
    prs = [
        _pr(1, files=[{"path": ".github/workflows/deploy.yml"}]),
        _pr(2, files=[{"path": "apps/x/migrations/0002_y.py"}]),
        _pr(3, files=[{"path": ".github/workflows/silent-failure-lint.yml"}]),
    ]
    kand, prot = k.waehle_kandidaten(prs, "IIL-Lotse", "achimdehnert")
    assert kand == [3]
    assert sum("Pruefrage-Klasse" in z for z in prot) == 2
