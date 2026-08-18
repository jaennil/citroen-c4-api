#!/usr/bin/env bash
# Снять статистику по параметрам из АРХИВА в кластере.
#
# Зачем: make_dashboard.py решает, какие панели рисовать, по тому, есть ли у
# параметра данные. Раньше он смотрел в локальный car.db - и это неверно.
# Локальный буфер периодически чистится (cleanup.py, вынос мусорных значений),
# а кластер хранит всю историю, и именно кластер - источник данных для Grafana.
# Из-за расхождения 17 параметров с данными в архиве не попадали на дашборд.
#
# Пишет cluster_stats.psv: имя|единица|значений|различных|мин|макс|последнее
#
#     ./fetch-stats.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="$HOME/.config/c4-can/env"
OUT="${1:-$HERE/cluster_stats.psv}"

[ -f "$CFG" ] || { echo "нет $CFG"; exit 1; }
# shellcheck disable=SC1090
source "$CFG"

SQL="select p.name, coalesce(p.unit,''), count(r.value), count(distinct r.value),
     coalesce(min(r.value),0), coalesce(max(r.value),0), coalesce(max(r.ts)::text,'-')
     from param p left join reading r on r.param_id=p.id group by 1,2 order by 1"

# psql внутри пода: port-forward тут не нужен и только добавляет хрупкости
ssh -n -i "$SSH_KEY" -p "$SSH_PORT" -o BatchMode=yes -o ConnectTimeout=8 "$SSH_HOST" \
    "kubectl -n citroen exec postgres-1 -c postgres -- psql -U postgres -d car -F'|' -tA -c \"$SQL\"" \
    > "$OUT.tmp" 2>/dev/null

n=$(wc -l < "$OUT.tmp")
if [ "$n" -lt 10 ]; then
  echo "не получилось снять статистику (строк $n) - кластер недоступен?"
  rm -f "$OUT.tmp"
  exit 1
fi
mv "$OUT.tmp" "$OUT"
echo "снято параметров: $n -> $OUT"
