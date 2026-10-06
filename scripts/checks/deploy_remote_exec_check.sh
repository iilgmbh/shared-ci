#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Waechter fuer die ssh-Schritte der Deploy-Reusables (shared-ci#88).
#
#  1. Keine Drittanbieter-ssh/scp-Action mehr: `grep -c appleboy` ueber
#     .github/workflows/_deploy-*.yml muss 0 sein (auch in Kommentaren — die
#     Zahl ist das Abnahmekriterium aus #88).
#  2. Die Composite remote-exec wird aus den Reusables per 40-stelligem SHA
#     referenziert (nie ./ — das waere der Checkout des Consumers — und nie
#     @main — dann wirkte jeder Push sofort auf alle gepinnten Consumer).
#  3. Der gepinnte SHA ist Vorfahre von HEAD und sein remote-exec-Verzeichnis
#     ist identisch mit dem in HEAD. Sonst testet der Selbsttest (der die
#     Composite aus HEAD faehrt) etwas anderes, als die Consumer ausfuehren.
#     Aendert ein PR die Composite, braucht er deshalb zwei Commits: erst die
#     Composite, dann den Pin auf deren SHA.
#
# Braucht die volle Historie (actions/checkout mit fetch-depth: 0).
# Am Ende ein Drill: dieselbe Zaehlung gegen eine praeparierte Kopie muss
# anschlagen — ein Waechter, der nie rot werden kann, ist keiner.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/../.."

ACTION_DIR=.github/actions/remote-exec
FILES=(.github/workflows/_deploy-*.yml)
rc=0

count_thirdparty() { cat "$@" | grep -c appleboy || true; }

n=$(count_thirdparty "${FILES[@]}")
if [ "$n" -ne 0 ]; then
  grep -n appleboy "${FILES[@]}" | sed 's/^/::error::/'
  echo "::error::$n Treffer 'appleboy' in den Deploy-Reusables (Soll 0, shared-ci#88)."
  rc=1
else
  echo "  ok  grep -c appleboy in ${FILES[*]} = 0"
fi

refs=$(grep -hoE 'uses:[[:space:]]+[^[:space:]]*actions/remote-exec[^[:space:]]*' "${FILES[@]}" \
         | sed -E 's/uses:[[:space:]]+//' | sort -u)
if [ -z "$refs" ]; then
  echo "::error::Keine Referenz auf remote-exec in den Deploy-Reusables — der Waechter prueft sonst nichts."
  rc=1
fi
while IFS= read -r ref; do
  [ -n "$ref" ] || continue
  if [[ ! "$ref" =~ ^iilgmbh/shared-ci/\.github/actions/remote-exec@([0-9a-f]{40})$ ]]; then
    echo "::error::$ref — erwartet iilgmbh/shared-ci/.github/actions/remote-exec@<40-stelliger SHA>."
    rc=1
    continue
  fi
  sha=${BASH_REMATCH[1]}
  if ! git cat-file -e "$sha^{commit}" 2>/dev/null; then
    echo "::error::$sha nicht im Repo (fetch-depth: 0? Commit gepusht?)."
    rc=1
  elif ! git merge-base --is-ancestor "$sha" HEAD; then
    echo "::error::$sha ist kein Vorfahre von HEAD — nach Squash-Merge haengt der Pin an einem verwaisten Commit. PR per Merge-Commit mergen."
    rc=1
  elif ! git diff --quiet "$sha" HEAD -- "$ACTION_DIR"; then
    echo "::error::$ACTION_DIR in HEAD weicht vom gepinnten $sha ab — Pin nachziehen:"
    git diff --stat "$sha" HEAD -- "$ACTION_DIR"
    rc=1
  else
    echo "  ok  remote-exec@$sha == $ACTION_DIR in HEAD"
  fi
done <<< "$refs"

# Drill: praeparierte Kopie mit einem Drittanbieter-Aufruf muss zaehlen.
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
{ cat "${FILES[0]}"; echo "        uses: appleboy/ssh-action@0000000000000000000000000000000000000000"; } > "$tmp"
if [ "$(count_thirdparty "$tmp")" -ne 1 ]; then
  echo "::error::Drill: praeparierter Treffer wurde nicht gezaehlt — Waechter blind."
  rc=1
else
  echo "  ok  Drill: praeparierter Treffer wird gezaehlt"
fi

exit "$rc"
