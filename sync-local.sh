#!/usr/bin/env bash
# Живая досылка в ЛОКАЛЬНЫЙ Postgres (local/docker-compose.yml) для Grafana на
# ноутбуке: без туннеля, каждые 2 с, задержка от машины до графика - секунды.
# Ведёт свой водяной знак в ~/.config/c4-can/local.state и не трогает флаг synced,
# который принадлежит досылке в кластер. Первый запуск на пустой базе перегонит
# весь car.db - это несколько минут, один раз.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB="${1:-$HERE/car.db}"
EVERY="${LOCAL_EVERY:-2}"
STATE="$HOME/.config/c4-can/local.state"
export CAR_PG="postgresql://car:car@127.0.0.1:5433/car"
cd "$HERE" || exit 0
stop=0; trap 'stop=1' TERM INT
echo "локальная досылка: каждые ${EVERY} с -> ${CAR_PG%%@*}@…"
while [ "$stop" = 0 ]; do
  out=$("$HERE/.venv/bin/python" "$HERE/sync.py" --sqlite "$DB" --state "$STATE" 2>&1) || {
    echo "$out" | tail -1; sleep 10 & wait $!; continue; }
  echo "$out" | grep -E "готово, отправлено [1-9]" || true
  sleep "$EVERY" & wait $!
done
"$HERE/.venv/bin/python" "$HERE/sync.py" --sqlite "$DB" --state "$STATE" 2>&1 | grep -E "готово" || true
