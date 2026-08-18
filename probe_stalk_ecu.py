"""
Попытка прочитать подрулевой COM2008P, воспроизводя преамбулу DiagBox дословно.

Перебор значений байта 8 в кадре смены сессии подвесил интерфейс (USBError 5,
устройство пришлось переткнуть), поэтому здесь ничего не выдумывается: шлём
ровно ту последовательность, которую DiagBox отправлял перед первым чтением
22D4xx, и сразу пробуем этот DID.

    ./.venv/bin/python probe_stalk_ecu.py
"""
import logging, sys
from lexia_boot_stalk import STALK_BOOT
from lexia_proto import Lexia, describe, parse_multi, read_multi_frame

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# 22D400 дворники, 22D401 свет и поворотники, 22D402 звуковой сигнал
TARGETS = [0xD400, 0xD401, 0xD402]

def main():
    lex = Lexia()
    if not lex.connect():
        return 1
    try:
        lex.drain()
        if not lex.device_boot(verbose=False):
            log.error("Нет связи: Lexia в OBD? зажигание?")
            return 3
        log.info(f"Проигрываю преамбулу подрулевого: {len(STALK_BOOT)} кадров")
        for i, hx in enumerate(STALK_BOOT, 1):
            try:
                lex.transact(bytes.fromhex(hx))
            except Exception as e:
                log.warning(f"  кадр {i} (cmd={hx[10:12]}) не прошёл: {type(e).__name__}")
        print()
        for did in TARGETS:
            try:
                payload, _ = lex.transact(read_multi_frame([did]))
            except Exception as e:
                print(f"  {did:04X}: сбой USB ({type(e).__name__})")
                continue
            got = parse_multi(payload, {did: 6})
            print(f"  {did:04X}: {describe(payload)}"
                  + (f"   сырое {got[did].hex()}" if got else ""))
        # контроль: не потеряли ли BSI
        from lexia_proto import read_did
        p, _ = lex.transact(read_did(0x70))
        print(f"\n  контроль BSI (D870): {describe(p)}")
    finally:
        lex.disconnect()
    return 0

if __name__ == "__main__":
    sys.exit(main())
