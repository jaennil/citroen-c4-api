#!/usr/bin/env bash
# Поднять локальную Grafana с локальным Postgres и положить туда актуальный дашборд.
# Дашборд - тот же make_dashboard.py, что и для кластера, только чистый JSON.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
"$ROOT/.venv/bin/python" "$ROOT/make_dashboard.py" > "$HERE/dashboards/citroen.json"
docker compose -f "$HERE/docker-compose.yml" up -d
IP=$(ip -4 -o addr show wlp2s0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)
echo "Grafana: http://localhost:3000/d/citroen-c4  (с телефона: http://${IP:-<ip>}:3000/d/citroen-c4)"
echo "Postgres: postgresql://car:car@127.0.0.1:5433/car"
