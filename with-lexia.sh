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

# Ждём, пока устройство станет реально нашим. Проверка боем: заявка на интерфейс
# либо проходит, либо нет - опрашивать журнал службы ненадёжно.
if ! "$HERE/.venv/bin/python" - "$WAIT" <<'PY'
import sys, time
import usb.core, usb.util
sys.path.insert(0, ".")
from lexia_proto import PRODUCT_ID, VENDOR_ID

until = time.time() + float(sys.argv[1])
while time.time() < until:
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is not None:
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except Exception:
            pass
        try:
            usb.util.claim_interface(dev, 0)
            usb.util.release_interface(dev, 0)
            usb.util.dispose_resources(dev)
            print("устройство свободно")
            sys.exit(0)
        except Exception:
            usb.util.dispose_resources(dev)
    time.sleep(1)
print("устройство так и не освободилось")
sys.exit(1)
PY
then
  echo "не дождался освобождения за ${WAIT} с. Сбор ещё держит USB или устройства нет на шине."
  echo "Проверь: lsusb -d 103a:f008"
  exit 1
fi

cd "$HERE" || exit 1
"$@"
rc=$?
echo "--- код возврата: $rc ---"
exit $rc
