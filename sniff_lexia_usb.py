"""
Сниффер USB-трафика Lexia 3 через usbmon.
Записывает все пакеты между DiagBox (VMware) и Lexia 3.

Использование:
  1. Запусти этот скрипт
  2. Передай Lexia в VMware
  3. В DiagBox сделай актуаторный тест (включи/выключи фары)
  4. Ctrl+C — остановить
  5. Лог сохранится в lexia_usb.log
"""

import struct
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Lexia 3 на Bus 1
USBMON_DEV = "/dev/usbmon1"
LEXIA_DEV_NUM = None  # определим автоматически
LOG_FILE = "lexia_usb.log"

# usbmon binary format (структура пакета usbmon)
# https://docs.kernel.org/usb/usbmon.html
# struct usbmon_packet {
#   u64 id;         // 8 bytes
#   unsigned char type; // 'S'ubmit, 'C'omplete
#   unsigned char xfer_type; // 0=ISO, 1=Intr, 2=Control, 3=Bulk
#   unsigned char epnum;
#   unsigned char devnum;
#   u16 busnum;
#   char flag_setup;
#   char flag_data;
#   s64 ts_sec;     // 8 bytes
#   s32 ts_usec;    // 4 bytes
#   int status;
#   unsigned length;
#   unsigned len_cap;
#   // setup[8] or error_count/numdesc
#   unsigned char setup[8];
# }
# total header = 64 bytes, then data follows

USBMON_HEADER = struct.Struct("=QBBBBHccqiIII8s")


def main():
    log.info(f"=== Lexia 3 USB Sniffer ===")
    log.info(f"Открываем {USBMON_DEV}...")

    try:
        f = open(USBMON_DEV, "rb")
    except PermissionError:
        log.error("Нужен root! Запусти: sudo python sniff_lexia_usb.py")
        sys.exit(1)

    log.info("Сниффер запущен!")
    log.info("Сейчас:")
    log.info("  1. Передай Lexia 3 в VMware")
    log.info("  2. В DiagBox открой актуаторный тест фар")
    log.info("  3. Включи/выключи фары")
    log.info("  4. Ctrl+C чтобы остановить")
    log.info("=" * 50)

    packets = []
    pkt_count = 0

    try:
        while True:
            header = f.read(USBMON_HEADER.size)
            if len(header) < USBMON_HEADER.size:
                continue

            (pkt_id, pkt_type, xfer_type, epnum, devnum,
             busnum, flag_setup, flag_data, ts_sec, ts_usec,
             status, length, len_cap, setup) = USBMON_HEADER.unpack(header)

            # Читаем данные пакета
            data = b""
            if len_cap > 0:
                data = f.read(len_cap)

            pkt_type_chr = chr(pkt_type)

            # Фильтруем: только Complete ('C') пакеты с данными
            # и только bulk transfer (xfer_type=3)
            if len(data) > 0 and xfer_type == 3:
                direction = "IN " if epnum & 0x80 else "OUT"
                ep = epnum & 0x7F
                ts = f"{ts_sec}.{ts_usec:06d}"

                pkt_info = {
                    "ts": ts,
                    "type": pkt_type_chr,
                    "dir": direction,
                    "ep": ep,
                    "dev": devnum,
                    "len": len(data),
                    "data": data.hex(),
                    "data_raw": data,
                }
                packets.append(pkt_info)
                pkt_count += 1

                # Показываем пакет
                data_preview = data.hex()[:80]
                log.info(f"  [{pkt_type_chr}] Dev:{devnum:3d} EP{ep} {direction} ({len(data):3d}B) {data_preview}")

                if pkt_count % 100 == 0:
                    log.info(f"  --- {pkt_count} пакетов ---")

    except KeyboardInterrupt:
        f.close()

        log.info(f"\nОстановлено. Всего пакетов: {pkt_count}")

        # Сохраняем
        with open(LOG_FILE, "w") as lf:
            lf.write("# Lexia 3 USB traffic dump\n")
            lf.write("# timestamp,type,direction,endpoint,device,length,hex_data\n")
            for p in packets:
                lf.write(f"{p['ts']},{p['type']},{p['dir']},{p['ep']},{p['dev']},{p['len']},{p['data']}\n")

        log.info(f"Сохранено в {LOG_FILE}")

        # Анализ
        if packets:
            devs = set(p['dev'] for p in packets)
            log.info(f"Устройства: {devs}")

            out_pkts = [p for p in packets if p['dir'] == 'OUT']
            in_pkts = [p for p in packets if p['dir'] == 'IN ']
            log.info(f"OUT (к Lexia): {len(out_pkts)}")
            log.info(f"IN (от Lexia): {len(in_pkts)}")

            if out_pkts:
                log.info("\nПервые 10 OUT-пакетов (команды DiagBox → Lexia):")
                for p in out_pkts[:10]:
                    log.info(f"  {p['data']}")


if __name__ == "__main__":
    main()
