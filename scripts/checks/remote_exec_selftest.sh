#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Selbsttest fuer .github/actions/remote-exec/remote-exec.sh (shared-ci#88).
#
# Startet einen sshd als aktueller Benutzer auf 127.0.0.1 (kein root, kein
# Container) und prueft das Verhalten, auf das sich die Deploy-Reusables
# verlassen:
#   - exec: Skript laeuft, envs kommen an (auch mit ' im Wert), stdin ist leer
#   - exec: Exit-Code != 0 schlaegt durch (set -euo pipefail im Skript)
#   - exec: command_timeout greift (Exit 124)
#   - copy: mehrere Quellen, strip_components, Ziel wird angelegt, ueberschreibt
#   - Host-Schluessel: richtiger Fingerabdruck ok, falscher = Abbruch,
#     known_hosts ok, ohne beides = Warnung + ok
#
# Aufruf:
#   remote_exec_selftest.sh            alle Faelle, Exit 0 nur wenn alle gruen
#   remote_exec_selftest.sh serve DIR  nur sshd starten (fuer den CI-Job, der
#                                      danach die Composite selbst aufruft);
#                                      schreibt DIR/env mit RX_T_*-Variablen
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/.github/actions/remote-exec/remote-exec.sh"
SSHD="$(command -v sshd || echo /usr/sbin/sshd)"

serve() {
  local d="$1" port
  mkdir -p "$d"
  chmod 700 "$d"
  ssh-keygen -q -t ed25519 -N '' -f "$d/host_ed25519" <<< y >/dev/null
  ssh-keygen -q -t ed25519 -N '' -f "$d/client" <<< y >/dev/null
  cp "$d/client.pub" "$d/authorized_keys"
  chmod 600 "$d/authorized_keys"
  port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
  cat > "$d/sshd_config" <<CFG
Port $port
ListenAddress 127.0.0.1
HostKey $d/host_ed25519
AuthorizedKeysFile $d/authorized_keys
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
UsePAM no
StrictModes no
PidFile $d/sshd.pid
CFG
  "$SSHD" -f "$d/sshd_config" -E "$d/sshd.log"
  for _ in $(seq 1 50); do
    ssh-keyscan -T 1 -p "$port" 127.0.0.1 >/dev/null 2>&1 && break
    sleep 0.2
  done
  # env = shell-quoted zum Sourcen, github_env = KEY=wert fuer $GITHUB_ENV
  {
    echo "RX_T_PORT=$port"
    echo "RX_T_USER=$(id -un)"
    echo "RX_T_FP=$(ssh-keygen -lf "$d/host_ed25519.pub" | awk '{print $2}')"
    echo "RX_T_KH=[127.0.0.1]:$port $(cut -d' ' -f1-2 "$d/host_ed25519.pub")"
  } > "$d/github_env"
  while IFS='=' read -r k v; do printf '%s=%q\n' "$k" "$v"; done < "$d/github_env" > "$d/env"
  echo "sshd laeuft auf 127.0.0.1:$port ($d)"
}

if [ "${1:-}" = serve ]; then
  serve "$2"
  exit 0
fi

D=$(mktemp -d)
trap '[ -f "$D/srv/sshd.pid" ] && kill "$(cat "$D/srv/sshd.pid")" 2>/dev/null; rm -rf "$D"' EXIT
serve "$D/srv" >/dev/null
# shellcheck disable=SC1091
. "$D/srv/env"
KEY="$(cat "$D/srv/client")"
export RUNNER_TEMP="$D"

pass=0; fail=0
ok()   { echo "  ok   $1"; pass=$((pass + 1)); }
bad()  { echo "  FAIL $1"; fail=$((fail + 1)); }
# chk <name> <befehl...> — ok, wenn der Befehl Exit 0 hat
chk()  { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }
out_has()     { grep -q -- "$1" "$D/out"; }
out_has_not() { ! grep -q -- "$1" "$D/out"; }
content_is()  { [ "$(cat "$1" 2>/dev/null)" = "$2" ]; }
no_workdir()  { ! compgen -G "$D/remote-exec.*" >/dev/null; }

# rx <erwarteter-exit> <name> [VAR=wert ...] — ruft remote-exec.sh mit Basis-Env
rx() {
  local want="$1" name="$2"; shift 2
  local rc=0
  ( cd "${CWD:-.}" && env -i PATH="$PATH" HOME="$HOME" RUNNER_TEMP="$RUNNER_TEMP" \
    RX_HOST="127.0.0.1:$RX_T_PORT" RX_USER="$RX_T_USER" RX_KEY="$KEY" \
    RX_FINGERPRINT="$RX_T_FP" "$@" bash "$SCRIPT" ) > "$D/out" 2>&1 || rc=$?
  if [ "$rc" = "$want" ]; then ok "$name (exit $rc)"; else bad "$name: exit $rc, erwartet $want"; sed 's/^/       | /' "$D/out"; fi
}

echo "remote-exec Selbsttest gegen 127.0.0.1:$RX_T_PORT"

ENV_SCRIPT=$(cat <<'SH'
set -euo pipefail
[ "$GHCR_TOKEN" = "to'ken \$x" ]
[ "$GHCR_USER" = bot ]
[ -z "${UNSET_VAR+x}" ]
echo remote-ok
SH
)
rx 0 "exec + envs mit Quote/\$" GHCR_TOKEN="to'ken \$x" GHCR_USER=bot RX_ENVS="GHCR_TOKEN,ghcr_user,UNSET_VAR" \
  RX_SCRIPT="$ENV_SCRIPT"
chk "exec: Remote-Ausgabe kommt an" out_has remote-ok
# shellcheck disable=SC2016  # soll erst remote expandieren
rx 0 "exec: stdin ist leer" RX_SCRIPT='set -euo pipefail; [ -z "$(cat)" ]'
rx 7 "exec: Exit-Code schlaegt durch" RX_SCRIPT=$'set -euo pipefail\nexit 7'
rx 1 "exec: pipefail schlaegt durch" RX_SCRIPT=$'set -euo pipefail\nfalse | true\necho nie'
chk "exec: bricht nach Fehler ab" out_has_not nie
rx 124 "exec: command_timeout greift" RX_COMMAND_TIMEOUT=2s RX_SCRIPT='sleep 20'
rx 2 "Host-Key: falscher Fingerabdruck bricht ab" RX_FINGERPRINT="SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" RX_SCRIPT='true'
chk "Host-Key: Meldung nennt mismatch" out_has 'fingerprint mismatch'
rx 0 "Host-Key: Fingerabdruck ohne SHA256:-Praefix" RX_FINGERPRINT="${RX_T_FP#SHA256:}" RX_SCRIPT='true'
rx 0 "Host-Key: known_hosts" RX_FINGERPRINT="" RX_KNOWN_HOSTS="$RX_T_KH" RX_SCRIPT='true'
rx 255 "Host-Key: falsches known_hosts bricht ab" RX_FINGERPRINT="" \
  RX_KNOWN_HOSTS="[127.0.0.1]:$RX_T_PORT ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" RX_SCRIPT='true'
rx 0 "Host-Key: ohne Pin = Warnung" RX_FINGERPRINT="" RX_SCRIPT='true'
chk "Host-Key: Warnung sichtbar" out_has '::warning'
rx 2 "Eingabe: leerer Host" RX_HOST="" RX_SCRIPT='true'
rx 2 "Eingabe: script und source zugleich" RX_SCRIPT='true' RX_SOURCE=x RX_TARGET=/tmp

# copy — relativ zum Arbeitsverzeichnis, wie im Deploy-Job
mkdir -p "$D/ws/deployment/scripts" "$D/remote"
( cd "$D/ws"
  echo a1 > docker-compose.prod.yml; echo b1 > .deploy-manifest.json; echo s1 > deployment/scripts/deploy-remote.sh
  echo alt > "$D/remote/docker-compose.prod.yml" )
CWD="$D/ws"
rx 0 "copy: Kommaliste + overwrite" \
  RX_SOURCE="docker-compose.prod.yml,.deploy-manifest.json" RX_TARGET="$D/remote/" RX_OVERWRITE=true
chk "copy: compose am Ziel ersetzt" content_is "$D/remote/docker-compose.prod.yml" a1
chk "copy: Manifest am Ziel" content_is "$D/remote/.deploy-manifest.json" b1
rx 0 "copy: strip_components 2, Ziel neu" \
  RX_SOURCE="deployment/scripts/deploy-remote.sh" RX_TARGET="$D/remote/neu/scripts/" RX_STRIP_COMPONENTS=2
chk "copy: strip_components legt Datei richtig ab" content_is "$D/remote/neu/scripts/deploy-remote.sh" s1
rx 2 "copy: keine passende Quelle" RX_SOURCE="gibtsnicht.yml" RX_TARGET="$D/remote/"
CWD=.
chk "Aufraeumen: kein Arbeitsverzeichnis mit Key bleibt liegen" no_workdir

echo "remote-exec Selbsttest: $pass ok, $fail FAIL"
[ "$fail" -eq 0 ]
