#!/usr/bin/env bash
# Взять Lexia на время одной команды и гарантированно вернуть сбору.
#
# Зачем. Служба c4-telemetry поднимается по udev и занимает устройство сразу при
# подключении. Если полезть к Lexia своим скриптом, не дождавшись, пока служба
# отпустит USB, получается драка за захват: I/O error, потом сброс шины, потом
# устройство вообще пропадает с шины и лечится только переподключением разъёма
# руками. За один вечер это случилось дважды - оба раза потому, что флаг паузы
# ставился и устройство трогалось почти одновременно.
#
# Здесь порядок правильный и его не надо помнить: поставить флаг, ДОЖДАТЬСЯ
# фактического освобождения, выполнить команду, снять флаг на любом выходе.
#
#     ./with-lexia.sh ./.venv/bin/python ecu.py --tx 6A8
#     ./with-lexia.sh ./.venv/bin/python poll_all.py --tx 6A8
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLAG="$HOME/.config/c4-can/pause"
WAIT="${LEXIA_WAIT:-30}"

[ $# -gt 0 ] || { echo "нечего запускать: ./with-lexia.sh <команда...>"; exit 2; }

mkdir -p "$(dirname "$FLAG")"
cleanup() { rm -f "$FLAG"; }
trap cleanup EXIT INT TERM
touch "$FLAG"

# Если служба не активна, ждать нечего: устройство ничьё, надо только убедиться,
# что оно на шине. Пробную заявку на интерфейс тут делать ВРЕДНО - захват и
# немедленное освобождение подряд ломает устройство, и следующая же команда
# рукопожатия падает с I/O error. Проверено: сразу после переподключения обёртка с
# пробным захватом валила device_boot, без него всё работает.
if systemctl is-active --quiet c4-telemetry; then
  echo "служба активна, жду пока отпустит USB..."
  ok=0
  for _ in $(seq 1 "$WAIT"); do
    if ! systemctl is-active --quiet c4-telemetry; then ok=1; break; fi
    # drive.py проверяет флаг раз в 3 с; ждём, пока он отпустит захват
    if journalctl -u c4-telemetry --since "-$((WAIT+5)) seconds" 2>/dev/null \
         | grep -q "освобождаю USB"; then ok=1; break; fi
    sleep 1
  done
  [ "$ok" = 1 ] || { echo "сбор так и не отпустил USB за ${WAIT} с"; exit 1; }
  sleep 2
fi

if ! lsusb -d 103a:f008 >/dev/null 2>&1; then
  echo "устройства нет на шине. Переткни USB-конец, при неудаче сними питание с OBD."
  exit 1
fi

cd "$HERE" || exit 1
"$@"
rc=$?
echo "--- код возврата: $rc ---"
exit $rc
