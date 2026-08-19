"""
Сброс залипшей Lexia без переподключения руками.

Нужен, потому что прерванная на середине команда оставляет устройство занятым, и
дальше любая запись валится USBError [Errno 5]. Раньше это лечилось только
выдёргиванием разъёма; release + dispose + reset поднимает его программно.

    ./.venv/bin/python reset_lexia.py
"""
import sys
import time

import usb.core
import usb.util

from lexia_proto import PRODUCT_ID, VENDOR_ID


def reset():
    d = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if d is None:
        print("устройства нет в USB")
        return 1
    for step, fn in (("release", lambda: usb.util.release_interface(d, 0)),
                     ("dispose", lambda: usb.util.dispose_resources(d)),
                     ("reset", d.reset)):
        try:
            fn()
            print(f"  {step}: ок")
        except Exception as e:
            print(f"  {step}: {type(e).__name__}")
    time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(reset())
