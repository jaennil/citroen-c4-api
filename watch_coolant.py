"""
Охота на отваливающийся датчик температуры ОЖ.

Задача другая, чем у drive.py, поэтому и скрипт отдельный. drive.py сидит на BSI
и навещает чужие блоки набегами - для интермиттирующей неисправности это
бесполезно: датчик пропадает на секунды, а вылазка в двигатель случается раз в
минуту и занимает несколько секунд. Здесь наоборот: заходим в двигатель ОДИН раз
и не уходим оттуда вовсе.

И это не просто удобнее, это ещё и безопаснее. Интерфейс укладывали не сами
чтения, а ПЕРЕКЛЮЧЕНИЯ блоков без закрытия сессии (см. CLAUDE.md, ff/02). Если
не переключаться, этой проблемы нет по построению.

Двух запросов хватает на всё, что нужно:

    21C08001   температура ОЖ (байт 6, смещение -50), обороты, время впрыска
    21CB8001   реле вентилятора, состояние вентилятора, заданная скорость обдува

То есть в одном кадре приезжают и причина, и следствие - можно увидеть, как
показание уходит и вентилятор включается. Пишется всё в ту же базу, что и
телеметрия, поэтому в Grafana появляется само.

Отдельно раз в полминуты читаются коды неисправностей: у P0116 интересен байт
статуса - момент, когда код становится АКТИВНЫМ, и есть то самое событие.

    ./with-lexia.sh ./.venv/bin/python watch_coolant.py --sqlite car.db
    ./with-lexia.sh ./.venv/bin/python watch_coolant.py --hz 4 --dtc-every 20
"""

import argparse
import logging
import signal
import sys
import time

from dtc_read import describe, parse_kwp
from ecu import enter, leave
from ecu_catalog import ECUS
from lexia_proto import Lexia
from poll_all import decode

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

ENGINE = (0x6A8, 0x688)
REQS = ["21C08001", "21CB8001"]

COOLANT = "MP_TEMPERATURE_D_EAU_MOTEUR_d"
FAN_RELAY = "MP_ETAT_RELAIS_GMV"
FAN_SPEED = "MP_CONSIGNE_VITESSE_GMV_C5"
RPM = "MP_REGIME_MOTEUR"

# Разумный коридор для прогретого двигателя. Выход за него - либо настоящий
# перегрев, либо подстановка аварийного значения вместо пропавшего датчика.
PLAUSIBLE = (60.0, 120.0)
# Скачок между соседними замерами, который физически невозможен: масса антифриза
# так быстро остыть или нагреться не может, значит это обрыв.
JUMP = 8.0

_stop = False


def _on_signal(signum, frame):
    global _stop
    _stop = True


def params_for(reqs):
    info = ECUS[ENGINE]
    out = {}
    for r in reqs:
        out[r] = [p for p in info["params"] if p["req"] == r]
    return info, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", help="писать в базу телеметрии")
    ap.add_argument("--hz", type=float, default=2.0, help="частота опроса")
    ap.add_argument("--dtc-every", type=float, default=30.0,
                    help="как часто читать коды неисправностей, с; 0 - не читать")
    ap.add_argument("--minutes", type=float, default=0,
                    help="остановиться через N минут (0 - до Ctrl+C)")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    info, by_req = params_for(REQS)
    total = sum(len(v) for v in by_req.values())
    log.info(f"двигатель {info['fam']}: {total} параметров из {len(REQS)} запросов")

    store = None
    if args.sqlite:
        from storage import Store
        store = Store(args.sqlite)

    lex = Lexia()
    if not lex.connect():
        return 1
    entered = False
    try:
        lex.drain()
        if not lex.device_boot(verbose=False):
            log.error("нет связи с машиной: зажигание включено?")
            return 3
        enter(lex, *ENGINE)
        entered = True
        log.info("вошли в блок двигателя и больше никуда не переключаемся")

        period = 1.0 / max(args.hz, 0.1)
        deadline = time.time() + args.minutes * 60 if args.minutes else None
        last_dtc = 0.0
        prev_coolant = None
        n = 0
        events = 0

        while not _stop and (deadline is None or time.time() < deadline):
            t0 = time.time()
            vals = {}
            for req, ps in by_req.items():
                if not ps:
                    continue
                try:
                    payload, _ = lex.read(bytes.fromhex(req))
                except Exception as e:
                    log.error(f"{req} сорвался: {type(e).__name__}: {e}")
                    payload = None
                if not payload:
                    log.warning(f"{req} промолчал")
                    continue
                if payload[0] == 0x7F:
                    log.warning(f"{req} отказ NRC {payload[2]:02X}")
                    continue
                for p in ps:
                    v = decode(p, payload)
                    if v is not None:
                        vals[p["name"]] = (v, p["unit"])

            if vals:
                n += 1
                c = vals.get(COOLANT, (None,))[0]
                fr = vals.get(FAN_RELAY, (None,))[0]
                fs = vals.get(FAN_SPEED, (None,))[0]
                rp = vals.get(RPM, (None,))[0]

                # то самое событие: показание ушло
                why = None
                if c is None:
                    why = "датчик не отдал значение вовсе"
                elif not (PLAUSIBLE[0] <= c <= PLAUSIBLE[1]):
                    why = f"значение вне коридора: {c:.0f} °C"
                elif prev_coolant is not None and abs(c - prev_coolant) >= JUMP:
                    why = f"скачок {prev_coolant:.0f} -> {c:.0f} °C за один замер"
                if why:
                    events += 1
                    log.warning(f"*** ДТОЖ: {why}   вентилятор реле={fr} задание={fs} "
                                f"обороты={rp}")
                if c is not None:
                    prev_coolant = c

                if store:
                    store.write([(0, f"{info['fam']}:{k}", u, v)
                                 for k, (v, u) in vals.items()])
                if n % max(1, int(args.hz * 10)) == 0:
                    log.info(f"замер {n}: ОЖ {c} °C, вентилятор реле={fr} задание={fs}%, "
                             f"обороты={rp}, событий {events}")

            # коды неисправностей: интересен момент, когда P0116 станет активным
            if args.dtc_every and time.time() - last_dtc >= args.dtc_every:
                last_dtc = time.time()
                try:
                    pl, _ = lex.read(b"\x17\xff\x00", deadline=8.0)
                    if pl and pl[0] == 0x57:
                        codes = parse_kwp(pl)
                        for code, failure, status, raw in codes:
                            act = "АКТИВЕН" if status & 0x01 else "сохранён"
                            log.info(f"код {code} статус {status:02X} {act}")
                            if store:
                                store.write([(0, f"DTC:{info['fam']}:{code}", "статус",
                                              float(status), describe(code, failure, status, raw))])
                except Exception as e:
                    log.warning(f"коды не прочитались: {type(e).__name__}")

            sleep = period - (time.time() - t0)
            if sleep > 0:
                time.sleep(sleep)

        log.info(f"остановлено. замеров {n}, событий с датчиком {events}")
    finally:
        # Закрыть сессию: незакрытые копятся и исчерпывают интерфейс.
        if entered:
            try:
                leave(lex)
            except Exception:
                pass
        try:
            lex.disconnect()
        except Exception:
            pass
        if store:
            store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
