"""Состояние машины через Lexia: обороты, скорость, напряжение, положение ключа."""
import logging
from lexia_proto import Lexia, read_did

log = logging.getLogger(__name__)

# did -> (название, длина, множитель, единица). start_byte=4 в базе = данные с 4-го
# байта ответа 62 D8 xx <данные>, то есть индекс 3 в нашей полезной нагрузке.
STATE = {
    0xBA8: ("обороты",    2, 0.125, "об/мин"),
    0xB61: ("скорость",   2, 0.01,  "км/ч"),
    0xA46: ("АКБ",        2, 0.001, "В"),
    0xA44: ("питание BSI",2, 0.001, "В"),
    0xD18: ("ключ",       1, 1.0,   ""),
    0xD03: ("состояние ГМП", 1, 1.0, ""),
}


def read_state(lex: Lexia) -> dict:
    """Прочитать параметры состояния. Ключи DID здесь 3-значные (D<xyz>)."""
    out = {}
    for did12, (name, ln, factor, unit) in STATE.items():
        hi = (did12 >> 8) & 0xF
        frame = _read3(lex, hi, did12 & 0xFF)
        if frame and frame[0] == 0x62:
            raw = frame[3:3 + ln]
            if len(raw) == ln:
                val = int.from_bytes(raw, "big") * factor
                out[name] = (val, unit, raw.hex())
    return out


def _read3(lex: Lexia, hi: int, lo: int):
    """Чтение DID вида 0xD<hi><lo>, например DBA8 -> hi=0xB, lo=0xA8."""
    from lexia_proto import frame as mkframe, READ_PREFIX
    payload = bytes([0x22, 0xD0 | hi, lo])
    p, _ = lex.transact(mkframe(READ_PREFIX, payload))
    return p
