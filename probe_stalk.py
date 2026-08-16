"""
Пробник подрулевого модуля (HDC / COM2008P) через ELM327 на разъёме OBD.

Самый дешёвый эксперимент во всём исследовании: ноль нового железа, ноль разрезов,
никакого риска. Снимает три неопределённости разом:
  1. какой из двух вариантов модуля стоит (KWP HDC_B71_V1 или UDS COM2008P);
  2. существуют ли биты дальнего и моргания там, где обещает база DiagBox;
  3. в каком байте они на ЭТОЙ машине (карту из интернета мерили на AEE2004).

Адрес подрулевого 0x742 / ответ 0x642 - из opendbc psa_aee2010_r3.dbc (Req_Diag_HDC).

Как пользоваться: запустить, и по подсказке щёлкать подрулевым. Скрипт опрашивает
модуль в цикле и подсвечивает изменившиеся биты - какой бит сменился при переводе
в дальний, тот и есть инвертор.

Запуск:  ./.venv/bin/python probe_stalk.py [порт]
"""

import logging
import sys
import time

import serial

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DEFAULT_PORT = "/dev/ttyACM0"
BAUD = 38400

# Два известных варианта подрулевого для семьи B7.
VARIANTS = [
    ("KWP  HDC_B71_V1", "0221C0", "свет ожидается в start_byte 3 (первый байт данных)"),
    ("UDS  COM2008P",   "0322D401", "свет ожидается в start_byte 4"),
]


class Elm:
    def __init__(self, port):
        self.ser = serial.Serial(port, BAUD, timeout=2)

    def cmd(self, c, wait=2.0):
        self.ser.reset_input_buffer()
        self.ser.write(f"{c}\r".encode())
        resp = b""
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.ser.in_waiting:
                resp += self.ser.read(self.ser.in_waiting)
                if b">" in resp:
                    break
            else:
                time.sleep(0.02)
        return resp.decode("ascii", errors="ignore").replace(">", "").strip()

    def init(self):
        for c in ("ATZ", "ATE0", "ATL0", "ATS0", "ATH1", "ATSP6", "ATCAF1"):
            self.cmd(c, 3 if c == "ATZ" else 1)
        self.cmd("ATSH742")
        self.cmd("ATCRA642")

    def close(self):
        self.ser.close()


def parse_bytes(resp: str):
    """Вытащить байты данных из ответа ELM (с заголовками ATH1)."""
    out = []
    for line in resp.splitlines():
        line = line.strip()
        if not line or "NO DATA" in line or "ERROR" in line or "SEARCHING" in line:
            continue
        toks = [t for t in line.replace("\t", " ").split() if len(t) == 2]
        try:
            vals = [int(t, 16) for t in toks]
        except ValueError:
            continue
        if len(vals) > 3:
            out.append(vals)
    return out[0] if out else None


def bits(v: int) -> str:
    return format(v, "08b")


def diff_mark(base: int, cur: int) -> str:
    """Показать двоично и пометить изменившиеся биты."""
    b, c = bits(base), bits(cur)
    marks = "".join("^" if x != y else " " for x, y in zip(b, c))
    return f"{c}  {marks}"


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PORT
    try:
        elm = Elm(port)
    except serial.SerialException as e:
        log.error(f"Не открыть {port}: {e}")
        log.error("USB-ELM обычно /dev/ttyACM0. Для Bluetooth: sudo rfcomm bind 0 <MAC>")
        return 1

    log.info(f"ELM327 на {port}, инициализация...")
    elm.init()
    log.info("Опрашиваю подрулевой на 0x742 (ответ 0x642)...")

    working = None
    for name, req, hint in VARIANTS:
        resp = elm.cmd(req, 3)
        vals = parse_bytes(resp)
        log.info(f"  {name}: {'ответ ' + ' '.join(f'{v:02X}' for v in vals) if vals else 'нет ответа'}")
        if vals:
            working = (name, req, hint, vals)
            break

    if not working:
        log.error("Подрулевой не ответил ни на KWP, ни на UDS.")
        log.error("Возможные причины: BSI не шлюзует запросы к HDC через OBD, другой адрес,")
        log.error("или зажигание выключено. Это не опровергает гипотезу - только означает,")
        log.error("что вариант придётся определять сниффом шины LS.CAR у самого разъёма.")
        elm.close()
        return 2

    name, req, hint, first = working
    log.info(f"Установлен вариант: {name}. {hint}")
    print()
    print("Теперь щёлкай подрулевым. Ctrl+C - выход.")
    print("Порядок для чистого результата: покой, габариты, ближний, ДАЛЬНИЙ, моргание.")
    print()
    print(f"{'байт':<6} {'hex':<5} двоично   изменившиеся биты")
    print("-" * 52)

    baseline = list(first)
    try:
        while True:
            resp = elm.cmd(req, 1.5)
            vals = parse_bytes(resp)
            if not vals:
                time.sleep(0.2)
                continue
            changed = [i for i in range(min(len(vals), len(baseline))) if vals[i] != baseline[i]]
            if changed:
                print(f"--- {time.strftime('%H:%M:%S')} ---")
                for i in changed:
                    print(f"  [{i}]   {vals[i]:02X}    {diff_mark(baseline[i], vals[i])}")
                baseline = list(vals)
            time.sleep(0.15)
    except KeyboardInterrupt:
        print()
        log.info("Готово. Бит, который взводился при переводе в дальний, - это инвертор.")
    finally:
        elm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
