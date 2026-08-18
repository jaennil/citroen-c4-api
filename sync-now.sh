#!/usr/bin/env bash
# Досылка накопленной телеметрии в кластер.
#
# Поднимает цепочку до Postgres одним соединением: SSH до домашнего сервера,
# который тут же выполняет kubectl port-forward, плюс проброс локального порта.
# Если сети нет или отправлять нечего - молча выходит с нулевым кодом, чтобы
# не спамить ошибками в journal.
#
# Доступы лежат в ~/.config/c4-can/env (режим 600, вне репозитория).
#
# Две грабли, на которых это уже спотыкалось:
#   * фоновому ssh обязателен -n, иначе он наследует stdin скрипта и умирает;
#   * отдельная "проверка готовности" отдельным подключением УБИВАЕТ туннель:
#     kubectl port-forward валится с "lost connection to pod" от резкого разрыва.
#     Поэтому никаких проб - первым и единственным подключается сам sync.py,
#     а ненадёжность лечится повторными попытками.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="$HOME/.config/c4-can/env"
DB="${1:-$HERE/car.db}"
TUNLOG=/tmp/c4-tunnel.log

[ -f "$CFG" ] || { echo "нет $CFG - доступы не настроены"; exit 0; }
# shellcheck disable=SC1090
source "$CFG"
[ -f "$DB" ] || { echo "нет базы $DB - нечего отправлять"; exit 0; }

cd "$HERE" || exit 0
PENDING=$("$HERE/.venv/bin/python" - "$DB" <<'PY'
import sys
from storage import Store
print(Store(sys.argv[1]).stats()["pending"])
PY
)
[ "${PENDING:-0}" -gt 0 ] || { echo "отправлять нечего"; exit 0; }
echo "к отправке: $PENDING значений"

SSH_OPTS=(-n -i "$SSH_KEY" -p "$SSH_PORT" -o BatchMode=yes -o ConnectTimeout=5)

# Сначала одна короткая проверка достижимости. Без неё вне домашней сети скрипт
# делал три попытки по таймауту SSH и висел больше минуты - чем и задерживал
# остановку службы сбора.
if ! ssh "${SSH_OPTS[@]}" -o ConnectTimeout=5 "$SSH_HOST" true >/dev/null 2>&1; then
  echo "дома не видно - отложено до следующего раза"
  exit 0
fi
export CAR_PG="postgresql://car:${CAR_PG_PASSWORD}@127.0.0.1:${LOCAL_PORT}/car"

TUNNEL=""
cleanup() { [ -n "$TUNNEL" ] && kill "$TUNNEL" 2>/dev/null; wait "$TUNNEL" 2>/dev/null; }
trap cleanup EXIT

for attempt in 1 2 3; do
  # остаток прошлого port-forward держит порт на сервере и роняет новый
  ssh "${SSH_OPTS[@]}" "$SSH_HOST" 'pkill -f "port-forward svc/postgres-rw"' >/dev/null 2>&1
  sleep 1

  ssh "${SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
      -L "${LOCAL_PORT}:localhost:${LOCAL_PORT}" "$SSH_HOST" \
      "kubectl -n citroen port-forward svc/postgres-rw ${LOCAL_PORT}:5432" \
      > "$TUNLOG" 2>&1 &
  TUNNEL=$!

  # ждём появления строки о готовности в логе самого port-forward
  ready=0
  for _ in $(seq 1 20); do
    kill -0 "$TUNNEL" 2>/dev/null || break
    grep -q "Forwarding from" "$TUNLOG" 2>/dev/null && { ready=1; break; }
    sleep 1
  done

  if [ "$ready" = 1 ] && "$HERE/.venv/bin/python" "$HERE/sync.py" --sqlite "$DB"; then
    exit 0
  fi

  echo "попытка $attempt не удалась:"; tail -2 "$TUNLOG" 2>/dev/null
  kill "$TUNNEL" 2>/dev/null; wait "$TUNNEL" 2>/dev/null; TUNNEL=""
  sleep 3
done

echo "не отправилось - данные остались в буфере, попробуем в следующий раз"
exit 0
