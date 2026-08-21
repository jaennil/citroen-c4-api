"""
Сбор телеметрии в поездке. Запустил - поехал - вернулся - синхронизировал.

Отличия от telemetry.py, важные именно для езды:

  * ПЕРЕПОДКЛЮЧЕНИЕ. За поездку связь рвётся: выключил и включил зажигание, дёрнул
    разъём на кочке, отвалился USB. telemetry.py при этом просто падает. Здесь
    любой обрыв ловится, соединение поднимается заново, сбор продолжается.
  * ДВЕ ЧАСТОТЫ. Полный снимок 316 параметров занимает ~4.4 с - для оборотов и
    скорости слишком грубо. Поэтому горячий набор опрашивается часто, а полный
    снимок делается раз в минуту.
  * ПОСТОЯННЫЙ ПУТЬ К БАЗЕ. Не /tmp: файл должен пережить перезагрузку.

Данные пишутся в SQLite и никуда не денутся без сети - sync.py потом досылает
их в кластер. Читать можно на ходу: это пассивные запросы, актуаторы не трогаются.

Запуск:
    ./.venv/bin/python drive.py                 # car.db рядом со скриптом
    ./.venv/bin/python drive.py --db /path/x.db --hot-hz 2
Остановка: Ctrl+C.
"""

import argparse
import logging
import os
import signal
import sys
import time

import usb.core

from did_catalog import BY_DID
from ecu import enter
from ecu_catalog import ECUS
from lexia_proto import Lexia, parse_multi, plan_batches, read_multi_frame
from poll_all import poll_ecu
from storage import Store
from telemetry import ALIASES, DRIVE_FULL_EVERY, DRIVE_HOT, decode, name_of

log = logging.getLogger("drive")

HERE = os.path.dirname(os.path.abspath(__file__))
# Файл-флаг для опытов: пока он есть, сбор не занимает USB и ждёт. Так не нужно
# каждый раз останавливать службу через sudo, чтобы поработать с Lexia руками.
PAUSE_FLAG = os.path.expanduser("~/.config/c4-can/pause")

# Частый лёгкий замер чужого блока: только эти параметры, а не весь каталог.
# Полный снимок двигателя - это 13 запросов и ~15 с, и раз в пять минут он слишком
# редок, чтобы поймать перемежающуюся неисправность датчика температуры ОЖ. Здесь
# читаются только те запросы, в которых лежат перечисленные параметры: два-три
# запроса, около секунды. Смысл именно в паре температур - основной, которой
# пользуется ЭБУ, и некорректированной: их расхождение и есть улика на врущий датчик.
# MP_TEMP_EAU_NON_CORRIGEE отсюда убран: запрос 21C78001 на этом блоке не отвечает,
# проверено на машине. Сравнить основную температуру с некорректированной не выйдет.
QUICK = {
    0x6A8: ("MP_TEMPERATURE_D_EAU_MOTEUR_d", "MP_REGIME_MOTEUR",
            "MP_RAPPORT_CYCLIQUE_MOTOVENTILATEUR"),
}


def quick_info(info, names):
    """Копия каталога блока, суженная до нужных параметров."""
    keep = [p for p in info["params"] if p["name"] in names]
    return dict(info, params=keep,
                requests=sorted({p["req"] for p in keep}))


_stop = False


def _on_signal(signum, frame):
    global _stop
    _stop = True
    log.info("останавливаюсь, дописываю буфер...")


def did_length(did: int) -> int:
    e = BY_DID.get(did)
    return max((x["sb"] - 4) + x["ln"] for x in e) if e else 1


def connect(retries=0):
    """Поднять связь с машиной. Возвращает Lexia или None."""
    lex = Lexia()
    if not lex.connect():
        return None
    try:
        lex.drain()
        if not lex.device_boot(verbose=False):
            lex.disconnect()
            return None
        lex.boot(verbose=False)
        return lex
    except Exception as e:
        log.warning(f"инициализация не удалась: {e}")
        try:
            lex.disconnect()
        except Exception:
            pass
        return None


def poll(lex, dids, lengths):
    values = {}
    for chunk in plan_batches(dids, lengths):
        payload, _ = lex.transact(read_multi_frame(chunk))
        values.update(parse_multi(payload, lengths))
    return values


def to_samples(values):
    out = []
    for d, raw in values.items():
        val, unit = decode(d, raw)
        if isinstance(val, (int, float)):
            out.append((d, name_of(d), unit, val))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "car.db"))
    ap.add_argument("--hot-hz", type=float, default=2.0)
    ap.add_argument("--full-every", type=float, default=DRIVE_FULL_EVERY)
    ap.add_argument("--log", default=os.path.join(HERE, "drive.log"))
    # Блоки с ПОДТВЕРЖДЁННОЙ сессией: у каждого в записи DiagBox за входом шли
    # настоящие чтения, и для каждого есть каталог параметров. Порядок = приоритет,
    # повтор адреса поднимает частоту: двигатель навещается вдвое чаще остальных,
    # потому что даёт больше всего меняющихся на ходу величин.
    #
    # Раньше здесь было пусто: вылазка подвешивала интерфейс и служба уходила в
    # бесконечный цикл, не собирая даже BSI. Причина найдена и устранена - перед
    # каждым чтением нужен кадр init, как это делает DiagBox (см. Lexia.read).
    ap.add_argument("--ecus",
                    default="6A8,747,6A8,742,6AD,75F,6C8,6B5,730,744,731,77B",
                    help="адреса чужих блоков через запятую, hex; пусто - только BSI")
    ap.add_argument("--ecu-every", type=float, default=300.0,
                    help="как часто снимать чужие блоки полностью, с")
    ap.add_argument("--quick-every", type=float, default=60.0,
                    help="как часто делать лёгкий замер (см. QUICK), с; 0 - выключить")
    ap.add_argument("--exit-after-idle", type=float, default=0,
                    help="выйти, если устройства нет столько секунд (0 - ждать вечно). "
                         "Нужно для автозапуска по udev: вынул Lexia - служба сама завершилась")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(args.log), logging.StreamHandler()])
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        from live_dids import LIVE
        all_dids = [d for d, _ in LIVE]
    except ImportError:
        all_dids = []
        log.warning("нет live_dids.py - полные снимки отключены, запусти sweep.py")
    hot_dids = [ALIASES[n] for n in DRIVE_HOT if n in ALIASES]
    lengths = {d: did_length(d) for d in set(all_dids) | set(hot_dids)}

    store = Store(args.db)
    log.info(f"база: {args.db} (уже накоплено: {store.stats()})")
    log.info(f"горячих параметров {len(hot_dids)} с частотой {args.hot_hz} Гц, "
             f"полный снимок {len(all_dids)} раз в {args.full_every:.0f} с")

    extra = []
    for tok in (args.ecus or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        want = int(tok, 16)
        key = next((k for k in ECUS if k[0] == want), None)
        if key is None:
            log.warning(f"блока 0x{want:03X} нет в каталоге - пропускаю")
        else:
            extra.append(key)
            log.info(f"дополнительно снимаю 0x{key[0]:03X} {ECUS[key]['ru']} "
                     f"({len(ECUS[key]['params'])} параметров) раз в {args.ecu_every:.0f} с")

    lex = None
    last_ecu = 0.0
    last_quick = 0.0
    n_ecu = n_quick = 0
    ecu_turn = 0
    last_full = 0.0
    period = 1.0 / args.hot_hz if args.hot_hz > 0 else 0.5
    n_hot = n_full = n_reconnect = 0
    idle_since = None
    t_report = time.time()

    paused = False
    while not _stop:
        if os.path.exists(PAUSE_FLAG):
            if not paused:
                log.info(f"пауза: есть файл {PAUSE_FLAG}, освобождаю USB и жду")
                if lex:
                    try: lex.disconnect()
                    except Exception: pass
                    lex = None
                # Освобождаем захват НЕ через объект: если disconnect упал на
                # ошибке ввода-вывода, дескриптор утекает и устройство остаётся
                # занятым при живом процессе. Находим заново и отпускаем принудительно.
                try:
                    import usb.core, usb.util
                    from lexia_proto import PRODUCT_ID, VENDOR_ID
                    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
                    if dev is not None:
                        try: usb.util.release_interface(dev, 0)
                        except Exception: pass
                        usb.util.dispose_resources(dev)
                except Exception as e:
                    log.warning(f"не удалось принудительно отпустить USB: {e}")
                paused = True
            time.sleep(3)
            continue
        if paused:
            log.info("пауза снята, продолжаю сбор")
            paused = False
        if lex is None:
            lex = connect()
            if lex is None:
                # Простой - это не только "разъёма нет". Залипшее устройство
                # открывается, но падает на инициализации, и раньше служба крутилась
                # в этом цикле бесконечно, потому что счётчик простоя не запускался.
                if idle_since is None:
                    idle_since = time.time()
                if args.exit_after_idle and time.time() - idle_since > args.exit_after_idle:
                    log.info(f"устройства нет {args.exit_after_idle:.0f} с - завершаюсь")
                    break
                time.sleep(3)
                continue
            idle_since = None
            n_reconnect += 1
            log.info(f"связь установлена (подключение №{n_reconnect})")

        t0 = time.time()
        # Вылазка в чужой блок. Делается редко и намеренно: переключение канала
        # плюс чтение занимают несколько секунд, и на это время частый опрос
        # оборотов встаёт. Раз в пять минут дырка в потоке BSI заметно реже
        # самого потока, а данные двигателя того стоят.
        # Лёгкий замер: дешёвый, поэтому часто. Идёт раньше полной вылазки, чтобы
        # не ждать её очереди.
        if (args.quick_every and QUICK and not os.path.exists(PAUSE_FLAG)
                and t0 - last_quick >= args.quick_every):
            last_quick = t0
            for tx, rx in extra:
                names = QUICK.get(tx)
                if not names:
                    continue
                info = quick_info(ECUS[(tx, rx)], names)
                if not info["params"]:
                    continue
                try:
                    enter(lex, tx, rx)
                    vals, _, _ = poll_ecu(lex, tx, rx, info)
                    store.write([(0, f"{info['fam']}:{n}", u, v)
                                 for n, (v, u) in vals.items()], ts=time.time())
                    n_quick += len(vals)
                except Exception as e:
                    log.warning(f"лёгкий замер {info['ru']} не удался ({type(e).__name__})")
                break          # только первый блок из списка, у которого есть QUICK
            try:
                enter(lex, 0x752, 0x652)
            except Exception:
                try: lex.disconnect()
                except Exception: pass
                lex = None
                continue

        # Проверка флага ещё и здесь: вылазка занимает до 15 с, и если начать её
        # с уже поставленным флагом, тот, кто просит USB, будет ждать всю вылазку.
        if extra and t0 - last_ecu >= args.ecu_every and not os.path.exists(PAUSE_FLAG):
            last_ecu = t0
            # По ОДНОМУ блоку за вылазку, по кругу. Обойти все тринадцать за раз -
            # это минуты, в которые не идёт ничего другого. Частоту отдельного
            # блока можно поднять, повторив его адрес в --ecus: список задаёт и
            # порядок, и вес, так что "6A8,6AD,6A8,747" навещает двигатель вдвое
            # чаще остальных.
            tx, rx = extra[ecu_turn % len(extra)]
            ecu_turn += 1
            info = ECUS[(tx, rx)]
            try:
                enter(lex, tx, rx)
                vals, refused, silent = poll_ecu(lex, tx, rx, info)
                store.write([(0, f"{info['fam']}:{n}", u, v)
                             for n, (v, u) in vals.items()], ts=time.time())
                n_ecu += len(vals)
                log.info(f"снимок {info['ru']}: {len(vals)} значений "
                         f"(отказ {refused}, молчание {silent})")
            except usb.core.USBError as e:
                # Устройство залипло: дальше сорвётся и обычный опрос BSI, поэтому
                # переподключаемся, а вылазки на этот сеанс прекращаем.
                log.warning(f"интерфейс перестал отвечать на {info['ru']} "
                            f"({type(e).__name__}) - вылазки отключены до перезапуска")
                extra = []
                try:
                    lex.disconnect()
                except Exception:
                    pass
                lex = None
                continue
            except Exception as e:
                # Блок не ответил - выкидываем только его, остальные не виноваты.
                log.warning(f"снимок {info['ru']} не удался ({type(e).__name__}: {e}) - "
                            f"этот блок больше не трогаю")
                extra = [k for k in extra if k != (tx, rx)]
            # Вернуть канал на BSI обязательно: иначе следующий же быстрый опрос
            # уйдёт в чужой блок и вернёт пустоту.
            try:
                enter(lex, 0x752, 0x652)
            except Exception as e:
                log.warning(f"не удалось вернуться на BSI ({type(e).__name__}) - переподключаюсь")
                try:
                    lex.disconnect()
                except Exception:
                    pass
                lex = None
                continue

        try:
            if all_dids and t0 - last_full >= args.full_every:
                vals = poll(lex, all_dids, lengths)
                store.write(to_samples(vals), ts=time.time())
                last_full = t0
                n_full += 1
            else:
                vals = poll(lex, hot_dids, lengths)
                store.write(to_samples(vals), ts=time.time())
                n_hot += 1
        except Exception as e:
            log.warning(f"обрыв связи ({e}) - переподключаюсь")
            try:
                lex.disconnect()
            except Exception:
                pass
            lex = None
            time.sleep(2)
            continue

        if time.time() - t_report >= 60:
            st = store.stats()
            log.info(f"собрано: {n_hot} быстрых, {n_full} полных, {n_ecu} полных и {n_quick} лёгких из чужих блоков, "
                     f"в базе {st['total']} значений по {st['params']} параметрам")
            t_report = time.time()

        dt = period - (time.time() - t0)
        if dt > 0:
            time.sleep(dt)

    if lex:
        try:
            lex.disconnect()
        except Exception:
            pass
    st = store.stats()
    store.close()
    log.info(f"готово. в базе {st['total']} значений по {st['params']} параметрам, "
             f"к отправке {st['pending']}")
    log.info("теперь: ./.venv/bin/python sync.py --sqlite " + args.db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
