"""
Чтение ЭБУ двигателя (CMM DCM7.1) - проверка переключения канала.

Разбор дампа показал: преамбулы перед чтением двигателя и BSI побайтово
одинаковы, кроме ОДНОГО байта - байта 8 в кадре смены сессии (cmd=01, 10 03):
    0x00 -> двигатель
    0x90 -> BSI

Проверяем на DID, которых у BSI НЕТ (22D411/D415/D417/D41B/D523). На 22D400-D403
проверять нельзя: у BSI это индексные DID, они отвечают СПИСКАМИ других DID и
выглядят как успех - на эти грабли я уже наступал.

    ./.venv/bin/python probe_engine.py
"""
import logging, sys
from lexia_boot_engine import ENGINE_BOOT, ENGINE_ONLY
from lexia_proto import Lexia, describe, parse_multi, read_multi_frame, read_did

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def main():
    lex = Lexia()
    if not lex.connect():
        return 1
    try:
        lex.drain()
        if not lex.device_boot(verbose=False):
            log.error("Нет связи: Lexia в OBD? зажигание включено?")
            return 3
        log.info(f"Проигрываю преамбулу двигателя: {len(ENGINE_BOOT)} кадров")
        for i, hx in enumerate(ENGINE_BOOT, 1):
            try:
                lex.transact(bytes.fromhex(hx))
            except Exception as e:
                log.warning(f"  кадр {i} (cmd={hx[10:12]}) не прошёл: {type(e).__name__}")

        print("\n=== DID, которых у BSI нет: отвечают только если мы на двигателе ===")
        hit = 0
        for did in ENGINE_ONLY:
            try:
                p, raw = lex.transact(read_multi_frame([did]))
            except Exception as e:
                print(f"  {did:04X}: сбой USB ({type(e).__name__})")
                continue
            ok = bool(p) and p[0] == 0x62
            if ok:
                hit += 1
            print(f"  {did:04X}: {describe(p)}")
            if p and p[0] == 0x62:
                print(f"        весь ответ: {raw.hex()[-40:]}")

        print(f"\nответили: {hit} из {len(ENGINE_ONLY)}")
        print("=== контроль: доступен ли ещё BSI (габариты D870) ===")
        try:
            p, _ = lex.transact(read_did(0x70))
            print(f"  D870: {describe(p)}")
        except Exception as e:
            print(f"  D870: сбой {type(e).__name__}")
    finally:
        lex.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
