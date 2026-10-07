#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# remote-exec.sh — Befehl oder Dateien per nativem ssh auf einen Host bringen
# (shared-ci#88, AUD-04 aus platform#3398).
#
# Ersetzt die Drittanbieter-Actions fuer ssh/scp in den Deploy-Reusables. Die
# Semantik ist ihnen bewusst nachgebaut, damit ein Tag-Bump keinen Deploy
# anders laufen laesst:
#
#   exec  Die Zeilen aus RX_SCRIPT laufen als EIN Kommando in der Login-Shell
#         des Zielbenutzers (wie `$SHELL -c`), davor `export NAME='wert'` fuer
#         jeden in RX_ENVS genannten und gesetzten Namen. Stdin ist leer.
#         Exit-Code der Remote-Shell = Exit-Code des Schritts.
#   copy  Quellen (Kommaliste, Glob, `!muster` = ausschliessen) lokal als
#         tar.gz packen, am Ziel `mkdir -p <target>` und
#         `tar -zxf - [--strip-components N] [--overwrite] -C <target>`.
#
#   Host   "host", "host:port" oder Kommaliste davon (nacheinander, erster
#          Fehler bricht ab).
#   Timeouts RX_TIMEOUT = Verbindungsaufbau (Default 30s), RX_COMMAND_TIMEOUT =
#          Laufzeit je Host (Default 10m); Ueberschreitung -> Exit 124.
#
# Host-Schluessel: StrictHostKeyChecking=yes gegen eine lauf-eigene known_hosts.
#   RX_KNOWN_HOSTS gesetzt   -> genau diese Zeilen sind die Wahrheit.
#   RX_FINGERPRINT gesetzt   -> ssh-keyscan, nur Schluessel mit genau diesem
#                               SHA256-Fingerabdruck landen in known_hosts;
#                               kein Treffer = Abbruch (wie bisher: mismatch).
#   beides leer              -> Schluessel werden beim ersten Kontakt
#                               uebernommen (TOFU) + ::warning::. Das ist das
#                               bisherige Verhalten ohne Fingerabdruck
#                               (keine Pruefung), jetzt sichtbar gemacht.
#
# Nichts landet in ~/.ssh: self-hosted Runner laufen als root auf Prod-Hosts,
# deren eigene known_hosts/Keys tabu sind. Alles liegt unter $RUNNER_TEMP und
# wird per trap entfernt. Werte (Key, envs) stehen nie in einer argv.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

die() { echo "::error title=remote-exec::$*" >&2; exit 2; }

: "${RX_HOST:=}"; : "${RX_USER:=}"; : "${RX_KEY:=}"
: "${RX_PORT:=22}"; : "${RX_FINGERPRINT:=}"; : "${RX_KNOWN_HOSTS:=}"
: "${RX_TIMEOUT:=30s}"; : "${RX_COMMAND_TIMEOUT:=10m}"
: "${RX_SCRIPT:=}"; : "${RX_ENVS:=}"
: "${RX_SOURCE:=}"; : "${RX_TARGET:=}"; : "${RX_STRIP_COMPONENTS:=0}"
: "${RX_OVERWRITE:=false}"

[ -n "$RX_HOST" ] || die "host ist leer (Secret nicht gesetzt?)"
[ -n "$RX_USER" ] || die "username ist leer (Secret nicht gesetzt?)"
[ -n "$RX_KEY" ]  || die "key ist leer (Secret nicht gesetzt?)"

if [ -n "$RX_SCRIPT" ] && [ -n "$RX_SOURCE" ]; then
  die "script und source schliessen sich aus — ein Aufruf ist entweder exec oder copy"
elif [ -n "$RX_SCRIPT" ]; then
  MODE="exec"
elif [ -n "$RX_SOURCE" ]; then
  MODE="copy"
  [ -n "$RX_TARGET" ] || die "copy braucht target"
  [[ "$RX_STRIP_COMPONENTS" =~ ^[0-9]+$ ]] || die "strip_components ist keine Zahl: $RX_STRIP_COMPONENTS"
else
  die "weder script noch source gesetzt"
fi

# Go-Dauer (30s, 10m, 1h30m, 90) -> Sekunden
to_seconds() {
  local d="$1" total=0 n u
  [[ "$d" =~ ^[0-9]+$ ]] && { echo "$d"; return; }
  [[ "$d" =~ ^([0-9]+[hms])+$ ]] || die "Dauer nicht lesbar: $d (erlaubt: 90, 30s, 10m, 1h30m)"
  while [[ "$d" =~ ^([0-9]+)([hms])(.*)$ ]]; do
    n=${BASH_REMATCH[1]}; u=${BASH_REMATCH[2]}; d=${BASH_REMATCH[3]}
    case "$u" in h) total=$((total + n * 3600)) ;; m) total=$((total + n * 60)) ;; s) total=$((total + n)) ;; esac
  done
  echo "$total"
}
CONNECT_S=$(to_seconds "$RX_TIMEOUT")
COMMAND_S=$(to_seconds "$RX_COMMAND_TIMEOUT")

WORK=$(mktemp -d "${RUNNER_TEMP:-/tmp}/remote-exec.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
chmod 700 "$WORK"
( umask 077
  # CRLF aus Secrets tolerieren und den Abschluss-Zeilenumbruch sicherstellen —
  # OpenSSH verweigert einen Key ohne ihn ("invalid format").
  printf '%s\n' "${RX_KEY//$'\r'/}" > "$WORK/key" )
KH="$WORK/known_hosts"
: > "$KH"

# Payload fuer exec: export-Zeilen (Quoting wie bisher: '…' mit '\'' fuer ')
# plus Skript. Geht per stdin, nicht per argv.
if [ "$MODE" = exec ]; then
  : > "$WORK/payload"
  chmod 600 "$WORK/payload"
  IFS=',' read -r -a _names <<< "$RX_ENVS"
  for name in "${_names[@]}"; do
    name="${name//[[:space:]]/}"
    [ -n "$name" ] || continue
    name="${name^^}"
    [[ "$name" =~ ^[A-Z_][A-Z0-9_]*$ ]] || die "envs: ungueltiger Name '$name'"
    if [ -n "${!name+x}" ]; then
      val="${!name}"
      printf "export %s='%s'\n" "$name" "${val//\'/\'\\\'\'}" >> "$WORK/payload"
    fi
  done
  printf '%s\n' "$RX_SCRIPT" >> "$WORK/payload"
fi

pin_host_key() {
  local host="$1" port="$2" scan match=0 line fp
  if [ -n "$RX_KNOWN_HOSTS" ]; then
    printf '%s\n' "$RX_KNOWN_HOSTS" >> "$KH"
    return 0
  fi
  scan=$(ssh-keyscan -T "$CONNECT_S" -p "$port" "$host" 2>/dev/null | grep -v '^#' || true)
  [ -n "$scan" ] || die "ssh-keyscan $host:$port lieferte keinen Host-Schluessel (Host/Port erreichbar?)"
  if [ -z "$RX_FINGERPRINT" ]; then
    echo "::warning title=remote-exec: Host-Schluessel ungeprueft::$host — kein Fingerabdruck/known_hosts uebergeben, Schluessel beim ersten Kontakt uebernommen (bisheriges Verhalten). Pinnen: Secret *_SSH_FINGERPRINT mit dem SHA256-Fingerabdruck setzen (ssh-keygen -lf <(ssh-keyscan $host))."
    printf '%s\n' "$scan" >> "$KH"
    return 0
  fi
  local want="$RX_FINGERPRINT"
  [[ "$want" == SHA256:* ]] || want="SHA256:$want"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    fp=$(printf '%s\n' "$line" | ssh-keygen -lf - 2>/dev/null | awk '{print $2}')
    if [ "$fp" = "$want" ]; then
      printf '%s\n' "$line" >> "$KH"
      match=1
    fi
  done <<< "$scan"
  [ "$match" -eq 1 ] || die "ssh: host key fingerprint mismatch fuer $host:$port — kein angebotener Schluessel hat $want"
}

run_on_host() {
  local spec="$1" host port
  spec="${spec//[[:space:]]/}"
  [ -n "$spec" ] || return 0
  if [[ "$spec" =~ ^([^:]+):([0-9]+)$ ]]; then
    host=${BASH_REMATCH[1]}; port=${BASH_REMATCH[2]}
  else
    host=$spec; port=$RX_PORT
  fi
  pin_host_key "$host" "$port"

  local -a SSH=(ssh -F /dev/null -p "$port" -i "$WORK/key"
    -o BatchMode=yes -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$KH" -o GlobalKnownHostsFile=/dev/null
    -o ConnectTimeout="$CONNECT_S" -o LogLevel=ERROR
    "$RX_USER@$host")

  if [ "$MODE" = exec ]; then
    echo "remote-exec: $RX_USER@$host:$port (Laufzeit max. ${COMMAND_S}s)"
    # eval "$(cat)" laeuft in der Login-Shell des Zielbenutzers — dieselbe Shell,
    # die bisher das Kommando bekam. Danach ist stdin leer, wie bisher.
    # shellcheck disable=SC2016  # soll erst remote expandieren
    timeout "$COMMAND_S" "${SSH[@]}" 'eval "$(cat)"' < "$WORK/payload"
  else
    local -a files=() excl=() pat tarx
    shopt -s nullglob
    IFS=',' read -r -a _src <<< "$RX_SOURCE"
    for pat in "${_src[@]}"; do
      pat="$(printf '%s' "$pat" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
      [ -n "$pat" ] || continue
      if [[ "$pat" == !* ]]; then
        # shellcheck disable=SC2206
        local m=( ${pat#!} ); excl+=("${m[@]}")
      else
        # shellcheck disable=SC2206
        local m=( $pat ); files+=("${m[@]}")
      fi
    done
    shopt -u nullglob
    [ "${#files[@]}" -gt 0 ] || die "copy: keine Datei passt auf '$RX_SOURCE'"
    local -a tarc=(tar)
    for pat in "${excl[@]}"; do tarc+=(--exclude "$pat"); done
    tarc+=(-zcf - "${files[@]}")
    tarx="tar -zxf -"
    [ "$RX_STRIP_COMPONENTS" -gt 0 ] && tarx+=" --strip-components $RX_STRIP_COMPONENTS"
    [ "$RX_OVERWRITE" = true ] && tarx+=" --overwrite"
    local q; q=$(printf '%q' "$RX_TARGET")
    echo "remote-copy: ${files[*]} -> $RX_USER@$host:$port:$RX_TARGET"
    "${tarc[@]}" | timeout "$COMMAND_S" "${SSH[@]}" "mkdir -p $q && $tarx -C $q"
  fi
}

IFS=',' read -r -a HOSTS <<< "$RX_HOST"
for h in "${HOSTS[@]}"; do
  run_on_host "$h"
done
