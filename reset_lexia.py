"""
Освобождение залипшей Lexia - по возможности БЕЗ сброса шины.

Прерванная на середине команда оставляет устройство занятым, и дальше любая
запись валится USBError [Errno 5]. Раньше это лечилось только выдёргиванием
разъёма.

Почему скрипт ступенчатый. Сначала здесь безусловно вызывался dev.reset(), и это
оказалось вредно: reset - это сброс на уровне ШИНЫ, устройство переподключается и
получает новый номер. От профилактических сбросов перед каждым запуском номер
уполз с 007 на 012, а потом устройство вообще перестало возвращаться на шину -
пропало из lsusb целиком при воткнутом кабеле и включённом зажигании, и подняло
его только переподключение USB руками.

Поэтому: сперва отпускаем захват, проверяем, ожило ли, и только если нет -
бьём по шине.

    ./.venv/bin/python reset_lexia.py           # мягко, с проверкой
    ./.venv/bin/python reset_lexia.py --hard    # сразу сброс шины
"""

import argparse
import sys
import time

import usb.core
import usb.util

from lexia_proto import POLL, PRODUCT_ID, VENDOR_ID, Lexia


def find():
    return usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)


def release(dev):
    """Отпустить захват, не трогая шину."""
    for step, fn in (("release", lambda: usb.util.release_interface(dev, 0)),
                     ("dispose", lambda: usb.util.dispose_resources(dev))):
        try:
            fn()
            print(f"  {step}: ок")
        except Exception as e:
            print(f"  {step}: {type(e).__name__}")


def alive() -> bool:
    """Проверка боем: открыть и послать опрос готовности."""
    lex = Lexia()
    if not lex.connect():
        return False
    try:
        lex._w(bytes.fromhex(POLL))
        time.sleep(0.01)
        return bool(lex._r(timeout=500))
    except Exception:
        return False
    finally:
        try:
            lex.disconnect()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hard", action="store_true",
                    help="сразу сброс шины (переподключает устройство)")
    args = ap.parse_args()

    # Не лезть к устройству, пока его может держать служба сбора: именно эта драка
    # за захват дважды сбрасывала Lexia с шины насовсем. Правильный путь -
    # with-lexia.sh, он ставит флаг и ждёт фактического освобождения.
    import os
    import subprocess
    flag = os.path.expanduser("~/.config/c4-can/pause")
    if not os.path.exists(flag):
        active = subprocess.run(["systemctl", "is-active", "--quiet", "c4-telemetry"],
                                check=False).returncode == 0
        if active:
            print("служба c4-telemetry активна, а флага паузы нет - она держит USB.")
            print("запускай через ./with-lexia.sh, иначе драка за захват уронит устройство.")
            return 2

    dev = find()
    if dev is None:
        print("устройства нет в USB. Если кабель воткнут и зажигание включено -")
        print("переткни USB-конец: после сброса шины устройство иногда не возвращается.")
        return 1

    if not args.hard:
        release(dev)
        time.sleep(0.5)
        if alive():
            print("устройство отвечает - сброс шины не нужен")
            return 0
        print("не отвечает после освобождения захвата, придётся сбросить шину")

    try:
        find().reset()
        print("  reset: ок")
    except Exception as e:
        print(f"  reset: {type(e).__name__}")
    time.sleep(2)
    print("устройство отвечает" if alive() else "по-прежнему молчит - переткни USB-конец")
    return 0


if __name__ == "__main__":
    sys.exit(main())
