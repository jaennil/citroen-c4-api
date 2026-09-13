#!/usr/bin/env bash
# SQL по базам DiagBox через контейнер Firebird 2.5.
#
# Базы в образе - Firebird ODS 11, это версия 2.5. Ставить сервер в систему не надо
# и нельзя лезть в них байтами: связь "код -> описание" реляционная, через ключи между
# таблицами, и сканирование по соседству байт даёт ерунду (проверено: для P0116 все
# пять кандидатов оказались про указатели поворота и сеть Комфорт).
#
# Базы КОПИРУЮТСЯ из образа в ~/life/citroen/diagbox-db: Firebird пишет в заголовок при
# подключении, а образ смонтирован только на чтение.
#
#     ./diagbox-sql.sh                       # поднять контейнер
#     ./diagbox-sql.sh DSD "SELECT ..."      # запрос к DSD.FDB
#     ./diagbox-sql.sh --stop
#
# Полезные таблицы DSD.FDB:
#   PARAM   PARID, PARSNAME (мнемоника), PARLNAME, PARDESCRIPTION
#   STATES  PARID, STASNAME (МНЕМОНИКА_ЗНАЧЕНИЕ), STALNAME (текст), STAVALUE (hex)
#   BLOCK, ECUVER, SERVICE, SERVUNIT - блоки, версии, службы
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_DIR="${DB_DIR:-$HOME/life/citroen/diagbox-db}"
IMG_DIR="${IMG_DIR:-/mnt/diagbox/AWRoot/dtrd/comm/data}"
NAME=fbdiag

if [ "${1:-}" = "--stop" ]; then docker rm -f "$NAME" >/dev/null 2>&1; echo "остановлен"; exit 0; fi

if ! docker ps --filter "name=$NAME" --format '{{.Names}}' | grep -q "$NAME"; then
  mkdir -p "$DB_DIR"
  for f in DSD.FDB GPC.FDB; do
    [ -f "$DB_DIR/$f" ] || { [ -f "$IMG_DIR/$f" ] || { echo "нет $IMG_DIR/$f - смонтируй образ"; exit 1; }
      cp "$IMG_DIR/$f" "$DB_DIR/" && chmod 666 "$DB_DIR/$f"; }
  done
  docker rm -f "$NAME" >/dev/null 2>&1
  docker run -d --name "$NAME" -v "$DB_DIR":/firebird/data jacobalberty/firebird:2.5-ss >/dev/null
  sleep 12
fi
# Пароль SYSDBA контейнер генерирует сам при первом старте и пишет в свой журнал.
PW=$(docker logs "$NAME" 2>&1 | grep -oE "password to '[^']+'" | head -1 | cut -d"'" -f2)
[ -n "$PW" ] || { echo "не нашёл пароль SYSDBA в журнале контейнера"; exit 1; }
[ $# -lt 2 ] && { echo "контейнер поднят. Пример: $0 DSD \"SELECT FIRST 5 PARSNAME FROM PARAM;\""; exit 0; }
printf 'SET LIST ON;\nSET NAMES WIN1252;\n%s\n' "$2" |
  docker exec -i "$NAME" /usr/local/firebird/bin/isql -u SYSDBA -p "$PW" \
    -ch WIN1252 "/firebird/data/$1.FDB"
