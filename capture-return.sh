#!/usr/bin/env bash
# Снять usbmon-дамп ИМЕННО возврата с KWP-блока на BSI - того места, где рушится
# канал. Одна команда, без интерактива: сам поднимает usbmon, сам стартует запись,
# сам делает ровно одну вылазку в двигатель с возвратом, сам останавливается.
#
# Зачем отдельно от capture-collect.sh: тот снимает работу СЛУЖБЫ и ждёт Ctrl+C,
# а здесь нужен короткий воспроизводимый эпизод - связь, один снимок двигателя,
# возврат, отказ - чтобы диффать с записью DiagBox покадрово.
#
# Проверено 2026-09-03: возврат падает USBTimeoutError ровно через 13 с, причём
# одинаково при трёх разных маршрутах (напрямую, через 0x747, через 0x6C1). Три
# версии причины оказались неверны, поэтому дальше только измерение.
#
# ПОРЯДОК ВАЖЕН. Флаг паузы должен стоять ДО подключения Lexia, иначе служба
# схватит устройство на середине перечисления и его придётся перетыкать. Скрипт
# ставит флаг сам и НЕ снимает - снимешь потом руками, когда закончишь:
#
#     rm ~/.config/c4-can/pause
#
# Запуск (двигатель должен работать, Lexia подключена и НЕ залипшая):
#
#     sudo ./capture-return.sh
#
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$HERE/return.log}"
RUN_AS="${SUDO_USER:-jaennil}"
FLAG="/home/$RUN_AS/.config/c4-can/pause"
SECS="${SECS:-45}"

[ "$(id -u)" = 0 ] || { echo "нужен root: sudo $0"; exit 1; }

mkdir -p "$(dirname "$FLAG")" && chown "$RUN_AS" "$(dirname "$FLAG")"
touch "$FLAG" && chown "$RUN_AS" "$FLAG"
echo "флаг паузы поставлен, служба должна припарковаться"

modprobe usbmon 2>/dev/null || true
[ -e /dev/usbmon0 ] || { echo "нет /dev/usbmon0"; exit 1; }

if ! lsusb -d 103a:f008 >/dev/null 2>&1; then
  echo "Lexia не на шине. Переткни USB-конец и запусти снова."; exit 1
fi

# Дождаться, пока служба реально отпустит дескриптор, иначе в дампе будет драка
# за захват, а не то, что мы ищем.
pid=$(systemctl show -p MainPID --value c4-telemetry 2>/dev/null || echo 0)
if [ -n "$pid" ] && [ "$pid" != "0" ]; then
  for _ in $(seq 1 30); do
    ls -l "/proc/$pid/fd" 2>/dev/null | grep -q "/dev/bus/usb" || break
    sleep 1
  done
  echo "служба USB не держит"
fi

echo "пишу дамп в $OUT, ${SECS} с"
python3 "$HERE/sniff_lexia_full.py" "$OUT" &
SNIFF=$!
sleep 2

# Один снимок двигателя и возврат. --ecu-every 5 чтобы вылазка случилась сразу,
# --ignore-pause потому что флаг стоит для службы, а не для нас.
echo "--- сбор пошёл ---"
sudo -u "$RUN_AS" timeout --signal=TERM "$((SECS - 5))" \
  "$HERE/.venv/bin/python" "$HERE/drive.py" --ignore-pause \
  --ecus 6A8 --ecu-every 5 --db /tmp/capret.db --log /tmp/capret.log 2>&1 |
  grep -E "связь установлена|снимок|возврат|не удал|Errno|Timeout" || true

sleep 2
kill -INT "$SNIFF" 2>/dev/null
wait "$SNIFF" 2>/dev/null
chown "$RUN_AS" "$OUT" 2>/dev/null

echo
echo "дамп: $OUT ($(du -h "$OUT" 2>/dev/null | cut -f1))"
echo "журнал сбора: /tmp/capret.log"
echo
echo "Lexia сейчас почти наверняка залипла - переткни USB-конец."
echo "Флаг паузы оставлен поднятым. Снять: rm $FLAG"
