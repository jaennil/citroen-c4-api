#!/usr/bin/env bash
# Живая досылка: пока идёт сбор, отправлять накопленное в кластер каждые LIVE_EVERY
# секунд через ОДИН постоянный туннель. Тогда Grafana с автообновлением показывает
# поездку с задержкой в 10-20 с, а не через 15 минут по таймеру.
#
# Запускается службой c4-sync-live вместе с c4-telemetry (BindsTo) и живёт, пока
# живёт сбор. По SIGTERM делает последнюю досылку и выходит - это и есть "досылка
# по событию": заглушил машину, выдернул Lexia - хвост поездки уехал сразу.
#
# Без интернета в машине просто ждёт: каждые 30 с проверяет, виден ли дом, и ничего
# не ломает. Досылка по таймеру (c4-sync) при живом режиме пропускает свой ход, см.
# sync-now.sh, чтобы два туннеля не дрались за port-forward на сервере.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="$HOME/.config/c4-can/env"
DB="${1:-$HERE/car.db}"
LIVE_EVERY="${LIVE_EVERY:-10}"
TUNLOG=/tmp/c4-tunnel-live.log
LOCK=/tmp/c4-sync.lock

[ -f "$CFG" ] || { echo "нет $CFG - доступы не настроены"; exit 0; }
# shellcheck disable=SC1090
source "$CFG"
cd "$HERE" || exit 0
export CAR_PG="postgresql://car:${CAR_PG_PASSWORD}@127.0.0.1:${LOCAL_PORT}/car"
SSH_OPTS=(-n -i "$SSH_KEY" -p "$SSH_PORT" -o BatchMode=yes -o ConnectTimeout=5)

TUNNEL=""
stop=0
tunnel_up() { [ -n "$TUNNEL" ] && kill -0 "$TUNNEL" 2>/dev/null; }
open_tunnel() {
  ssh "${SSH_OPTS[@]}" "$SSH_HOST" true >/dev/null 2>&1 || return 1
  ssh "${SSH_OPTS[@]}" "$SSH_HOST" 'pkill -f "port-forward svc/postgres-rw"' >/dev/null 2>&1
  sleep 1
  ssh "${SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
      -o ServerAliveCountMax=3 \
      -L "${LOCAL_PORT}:localhost:${LOCAL_PORT}" "$SSH_HOST" \
      "kubectl -n citroen port-forward svc/postgres-rw ${LOCAL_PORT}:5432" \
      > "$TUNLOG" 2>&1 &
  TUNNEL=$!
  for _ in $(seq 1 20); do
    kill -0 "$TUNNEL" 2>/dev/null || return 1
    grep -q "Forwarding from" "$TUNLOG" 2>/dev/null && return 0
    sleep 1
  done
  return 1
}
close_tunnel() { [ -n "$TUNNEL" ] && { kill "$TUNNEL" 2>/dev/null; wait "$TUNNEL" 2>/dev/null; }; TUNNEL=""; }
# Один прогон sync.py под замком: та же блокировка у sync-now.sh, чтобы две
# досылки не писали в базу одновременно.
push() { flock -w 30 "$LOCK" "$HERE/.venv/bin/python" "$HERE/sync.py" --sqlite "$DB" 2>&1 | grep -E "готово|ошибк|Error|не удал" ; }
on_term() { stop=1; }
trap on_term TERM INT

echo "живая досылка: каждые ${LIVE_EVERY} с"
while [ "$stop" = 0 ]; do
  if ! tunnel_up; then
    if open_tunnel; then echo "туннель открыт"; else close_tunnel; sleep 30 & wait $!; continue; fi
  fi
  push
  sleep "$LIVE_EVERY" & wait $!
done
# последняя досылка после остановки сбора - хвост поездки
echo "сбор остановлен - досылаю хвост"
tunnel_up || open_tunnel
tunnel_up && push
close_tunnel
exit 0
