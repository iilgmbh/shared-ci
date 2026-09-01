#!/usr/bin/env bash
# Prueft die Datei-Auswahl in _deploy-unified.yml gegen Fixtures (platform#2586 K3).
#
# Diese Auswahl entscheidet, welche compose-Dateien der Deploy zum Host traegt
# und in die `-f`-Kette nimmt. Faellt eine Datei heraus, werden ihre Container
# beim naechsten `up --remove-orphans` GELOESCHT — genau so verschwanden am
# 2026-09-01 `mcp_hub_rag`, `llm_gateway` und `mcp_hub_grafana`.
#
# Der Check EXTRAHIERT den Block zwischen den Markern aus dem Workflow und
# fuehrt ihn aus. Eine Kopie der Logik im Test wuerde vom Workflow wegdriften
# und dann nichts mehr ueber den echten Deploy belegen
# (🌀 feedback_active_copy_without_source_invisible_to_drift).
#
# Verschwinden die Marker, scheitert der Check laut — nicht still.
set -euo pipefail

WURZEL="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKFLOW="$WURZEL/.github/workflows/_deploy-unified.yml"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BLOCK="$TMP/auswahl.sh"
sed -n '/# --- AUSWAHL-ANFANG/,/# --- AUSWAHL-ENDE/p' "$WORKFLOW" \
  | sed 's/^ *//' > "$BLOCK.roh"

if ! grep -q "COMPOSE_LISTE=()" "$BLOCK.roh"; then
  echo "❌ Auswahl-Block nicht gefunden — Marker AUSWAHL-ANFANG/-ENDE fehlen in"
  echo "   $WORKFLOW. Ohne sie prueft dieser Check nichts."
  exit 1
fi

{
  cat "$BLOCK.roh"
  echo 'printf "%s\n" "${COMPOSE_LISTE[@]:-}"'
} > "$BLOCK"

fehler=0

pruefe() {
  local name="$1" erwartet="$2"
  shift 2
  local d="$TMP/$name"
  mkdir -p "$d"
  for f in "$@"; do : > "$d/$f"; done
  local ist
  # `|| true`: bei leerer Auswahl liefert grep Exit 1 und wuerde unter
  # `set -e` den ganzen Check abbrechen — die leere Auswahl ist aber ein
  # gueltiger Fall, den wir gerade pruefen wollen.
  ist="$(cd "$d" && bash "$BLOCK" | grep -v '^$' | sort | tr '\n' ' ' | sed 's/ $//' || true)"
  if [ "$ist" = "$erwartet" ]; then
    echo "  ✅ $name: '$ist'"
  else
    echo "  ❌ $name: erwartet '$erwartet', bekommen '$ist'"
    fehler=1
  fi
}

echo "Auswahl der prod-relevanten compose-Dateien (aus _deploy-unified.yml)"

# Der Anlass: die Overlay-Datei MUSS mitfahren.
pruefe overlay_faehrt_mit \
  "docker-compose.llm-mcp.yml docker-compose.prod.yml" \
  docker-compose.prod.yml docker-compose.llm-mcp.yml

# Neben einer prod-Datei ist die nackte Datei die Entwicklungs-Datei.
pruefe nackte_faellt_weg_neben_prod \
  "docker-compose.prod.yml" \
  docker-compose.yml docker-compose.prod.yml

# Gegenprobe: ohne prod-Datei ist die nackte Datei der einzige Stack (chat-hub).
pruefe nackte_bleibt_ohne_prod \
  "docker-compose.yml" \
  docker-compose.yml

# Andere Umgebungen gehoeren nicht auf den Prod-Host.
pruefe fremde_umgebungen_raus \
  "docker-compose.prod.yml" \
  docker-compose.prod.yml docker-compose.staging.yml docker-compose.dev.yml

# Reste, die auf Hosts liegenbleiben (gesehen in /opt/mcp-hub).
pruefe backup_datei_raus \
  "docker-compose.prod.yml" \
  docker-compose.prod.yml docker-compose.llm-mcp.yml.bak

# Gegenprobe zur Leer-Antwort: ein Verzeichnis ohne compose-Datei gibt nichts
# aus — dieselbe Ausgabe wie ein kaputter Block, darum ausdruecklich geprueft.
pruefe leeres_verzeichnis "" README.md

if [ "$fehler" = 0 ]; then
  echo "✅ Auswahl wie erwartet — Overlay-Dateien fahren mit"
else
  echo "❌ Auswahl weicht ab — Container koennten beim Deploy geloescht werden"
fi
exit "$fehler"
