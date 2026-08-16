"""
Полный сниффер USB-трафика Lexia 3 через usbmon.

Отличие от sniff_lexia_usb.py, который снял старый дамп: тот писал ТОЛЬКО bulk-передачи
с непустыми данными (`if len(data) > 0 and xfer_type == 3`). Из-за этого вся
vendor-специфичная инициализация устройства через control-передачи (xfer_type=2)
и все пакеты нулевой длины оказались невидимы. Именно поэтому воспроизведение
одних bulk-кадров не поднимает связь с машиной - статус остаётся 0x0C.

Здесь пишется ВСЁ: control, interrupt, bulk, isochronous, в обе стороны, включая
setup-пакеты control-передач и пакеты нулевой длины.

Запуск (нужен root, модуль usbmon должен быть загружен):
    sudo modprobe usbmon
    sudo python sniff_lexia_full.py [выходной_файл]

Порядок съёмки, важен именно такой:
  1. Lexia ОТКЛЮЧЕНА от виртуалки (или ещё не воткнута в USB).
  2. Запустить этот сниффер.
  3. Воткнуть Lexia / передать её в VMware - попадёт вся энумерация.
  4. Запустить DiagBox, выбрать автомобиль, дойти до BSI.
  5. Сделать тест актуатора ДАЛЬНЕГО СВЕТА (feux de route), потом ближнего.
  6. Ctrl+C.

Этот дамп разом закрывает два вопроса: настоящую инициализацию и подлинный
идентификатор актуатора дальнего света - без всяких догадок из чужих баз.
"""

import logging
import struct
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

USBMON_DEV = "/dev/usbmon0"  # 0 = все шины сразу, отфильтруем потом по устройству
DEFAULT_OUT = "lexia_full.log"

LEXIA_VID_PID = "103a:f008"

# struct usbmon_packet, 48 байт, затем len_cap байт данных
USBMON_HEADER = struct.Struct("=QBBBBHccqiIII8s")

XFER = {0: "ISO", 1: "INT", 2: "CTRL", 3: "BULK"}


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    try:
        f = open(USBMON_DEV, "rb")
    except PermissionError:
        log.error("Нужен root: sudo python sniff_lexia_full.py")
        return 1
    except FileNotFoundError:
        log.error(f"{USBMON_DEV} нет. Сначала: sudo modprobe usbmon")
        return 1

    log.info("Сниффер запущен. Пишу ВСЁ: control, bulk, interrupt, включая пустые пакеты.")
    log.info("Теперь втыкай Lexia / передавай её в VMware, потом запускай DiagBox.")
    log.info("Не забудь в DiagBox выбрать авто и сделать тест ДАЛЬНЕГО света. Ctrl+C - стоп.")

    # Пишем на диск сразу, а не копим в памяти: съёмка длинная, и потерять её
    # из-за kill -9 или падения нельзя.
    lf = open(out_path, "w", buffering=1)
    lf.write("# Lexia 3 full USB dump (control+bulk+interrupt, включая пустые)\n")
    lf.write("# ts,type,xfer,dir,ep,bus,dev,status,length,caplen,setup,data\n")

    n_rows = 0
    counts = {}
    try:
        while True:
            header = f.read(USBMON_HEADER.size)
            if len(header) < USBMON_HEADER.size:
                continue
            (pkt_id, pkt_type, xfer_type, epnum, devnum, busnum,
             flag_setup, flag_data, ts_sec, ts_usec,
             status, length, len_cap, setup) = USBMON_HEADER.unpack(header)

            data = f.read(len_cap) if len_cap > 0 else b""

            kind = XFER.get(xfer_type, str(xfer_type))
            direction = "IN" if epnum & 0x80 else "OUT"
            ep = epnum & 0x7F
            # setup-пакет валиден только у control-передач
            setup_hex = setup.hex() if (xfer_type == 2 and flag_setup == b"\x00") else ""

            lf.write(",".join(str(x) for x in (
                f"{ts_sec}.{ts_usec:06d}", chr(pkt_type), kind, direction,
                ep, busnum, devnum, status, length, len(data),
                setup_hex, data.hex())) + "\n")
            n_rows += 1

            key = (busnum, devnum, kind)
            counts[key] = counts.get(key, 0) + 1

            if n_rows % 500 == 0:
                log.info(f"  ... {n_rows} пакетов, пишу в {out_path}")
    except KeyboardInterrupt:
        pass
    finally:
        f.close()
        lf.close()

    log.info(f"Сохранено {n_rows} пакетов в {out_path}")
    log.info("Пакетов по (шина, устройство, тип):")
    for (bus, dev, kind), n in sorted(counts.items(), key=lambda x: -x[1])[:15]:
        log.info(f"  bus {bus} dev {dev:3d} {kind:<5} x{n}")
    log.info(f"Найди в списке устройство Lexia ({LEXIA_VID_PID}) - его номер видно в lsusb.")
    log.info("Обрати внимание на строки CTRL: это и есть та инициализация, которой нам не хватало.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
