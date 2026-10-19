#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Waechter: Release-Tags von shared-ci sind leichte Tags (shared-ci#112).
#
# _ci-python.yml ruft _review.yml lokal auf (`uses: ./.github/workflows/...`).
# Unter einem annotierten Tag loest GitHub diesen Aufruf auf den SHA des
# Tag-Objekts statt des Commits auf; jeder Push- oder Dispatch-Lauf eines
# Konsumenten endet dann mit startup_failure und 0 Jobs, ohne Annotation.
# PR-Laeufe bleiben gruen, deshalb faellt es im PR nicht auf. Probe 19.10.:
# derselbe Commit, annotiert 0 Jobs, leicht 14 Jobs.
#
# Fragt die Ref ueber die API ab, nicht ueber den lokalen Checkout: Das ist
# dieselbe Sicht, die Actions beim Aufloesen hat.
#
# Aufruf: tag_art_check.sh <tag>   (braucht GH_TOKEN und GITHUB_REPOSITORY)
# Exit 1, wenn der Tag annotiert ist; die Meldung nennt den Ersatz.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

tag="${1:?Aufruf: tag_art_check.sh <tag>}"
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY fehlt}"

art=$(gh api "repos/${repo}/git/ref/tags/${tag}" --jq .object.type)

if [ "$art" = commit ]; then
  echo "ok: ${tag} ist ein leichter Tag"
  exit 0
fi

commit=$(gh api "repos/${repo}/commits/${tag}" --jq .sha)
echo "::error::${tag} ist ein annotierter Tag (Objekt: ${art}). Konsumenten starten darunter keinen Push- oder Dispatch-Lauf (shared-ci#112). Ersatz als leichter Tag auf ${commit}: git tag -f ${tag} ${commit} && git push -f origin refs/tags/${tag}"
exit 1
