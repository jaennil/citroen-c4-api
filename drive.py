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

import usb.core, usb.util

from did_catalog import BY_DID
from ecu import enter
from ecu_catalog import ECUS
from lexia_proto import Lexia, parse_multi, plan_batches, read_multi_frame
from poll_all import SKIP_BLOCKS, load_dead, poll_ecu, save_dead
from schedule import MIN_GAP, SCHEDULE
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


def read_dtc(lex, tx, rx, info, store):
    """Коды одного блока в базу телеметрии как временной ряд.

    Пишем не только найденные коды, но и НОЛЬ для тех, что у этого блока уже
    встречались раньше, а сейчас в ответе их нет. Без нулей в Grafana получаются
    редкие точки вместо ряда: не видно ни когда код появился, ни когда ушёл, и
    корреляцию с температурой или оборотами не построить.
    """
    from dtc_read import describe, read_block
    codes, raw, ok = read_block(lex, tx, rx)
    if not ok:
        # Блок промолчал или отказал. НИЧЕГО не пишем: раньше сюда попадало молчание
        # двигателя (обычное дело), и нули затирали живые коды - в истории P0116
        # прыгал 1-0-1-0, хотя код никуда не девался.
        why = f"отказ NRC {raw[2]:02X}" if raw and len(raw) > 2 and raw[0] == 0x7F else "молчит"
        log.info(f"коды {info['ru']}: {why}, пропускаю")
        return 0
    ts = time.time()

    def key(c, f):
        # Тип отказа - часть кода, а не украшение: у PSA B1137 с типами 01/02/04 это
        # три разные неисправности (обрыв, замыкание на массу, на плюс). Без него они
        # схлопывались в одну строку и затирали друг друга.
        return f"DTC:{info['fam']}:{c}" + (f"-{f:02X}" if f is not None else "")

    rows = [(0, key(c, f), "статус", float(st), describe(c, f, st, rw, info["fam"]))
            for c, f, st, rw in codes]
    seen = {key(c, f) for c, f, _, _ in codes}
    # известные коды этого блока, которых сейчас нет - ноль, "кода нет"
    known = store.names_like(f"DTC:{info['fam']}:%")
    rows += [(0, n, "статус", 0.0) for n in known if n not in seen]
    store.write(rows, ts=ts)
    log.info(f"коды {info['ru']}: {len(codes)} "
             f"({', '.join(f'{c}{" активен" if st & 1 else ""}' for c, _, st, _ in codes) or 'нет'})")
    return len(rows)


def connect(settle=0.0):
    """Поднять связь с машиной. Возвращает Lexia или None.

    settle - пауза между захватом устройства и первой командой. Она нужна: udev
    поднимает службу в тот же миг, как устройство появилось на шине, а прошивке
    интерфейса надо успеть встать. Замерено на машине трижды: команда через
    50 мс после появления кладёт устройство наглухо (дальше USBError [Errno 5] на
    всё и лечится только переподключением разъёма), а та же команда после
    ручной паузы проходит нормально.
    """
    lex = Lexia()
    if not lex.connect():
        return None
    try:
        if settle:
            time.sleep(settle)
        lex.drain()
        if not lex.device_boot(verbose=False):
            lex.disconnect()
            return None
        # Вход в BSI нынешним способом, а НЕ старой lex.boot().
        #
        # boot() - это обвязка уровня автомобиля из времён, когда переключения
        # блоков ещё не умели: она сама настраивает канал по-своему. Теперь канал
        # задаёт enter(), и два способа конфликтуют. Симптом ровно такой: связь
        # "устанавливается", полный снимок BSI отрабатывает и записывает НОЛЬ
        # значений, а через минуту чтения уходят в таймаут и интерфейс залипает.
        # Ручная проверка тем же временем работала именно потому, что делала enter().
        enter(lex, 0x752, 0x652)
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
    # Формат: ADDR[:СЕКУНД], через запятую. Секунды - минимальный интервал между
    # снимками ЭТОГО блока; без них берётся --ecu-every. Планировщик на каждом
    # такте берёт самый просроченный блок, так что двигатель можно читать каждые
    # две минуты, а подушки - раз в час, и список не надо раздувать повторами.
    # Без --ecus работает расписание из schedule.py (задачи с наборами параметров
    # и своими интервалами). --ecus ADDR[:секунд] - ручной список целых блоков
    # для разовых проверок, он заменяет расписание.
    ap.add_argument("--ecus", default="",
                    help="ручной список блоков ADDR[:секунд], hex; пусто - расписание schedule.py")
    # ПОЛНАЯ вылазка по умолчанию ВЫКЛЮЧЕНА (0). Она кладёт интерфейс: проверено
    # трижды на машине, через 30-60 с после начала обхода двигателя приходит
    # USBTimeoutError, и дальше устройство отвечает USBError [Errno 5] на всё до
    # переподключения разъёма. Лёгкий замер (QUICK) при этом проходит стабильно,
    # поэтому он и остаётся включённым: BSI собирается, температура ОЖ и обороты
    # двигателя пишутся раз в минуту, а рискованный обход включается вручную
    # флагом --ecu-every 300, когда есть кому смотреть за результатом.
    ap.add_argument("--ecu-every", type=float, default=0.0,
                    help="минимальный зазор между вылазками, с (расписание: MIN_GAP); "
                         "для --ecus без :секунд - и их интервал; 0 - вылазки выключены")
    # Лёгкий замер тоже ВЫКЛЮЧЕН по умолчанию. Измерено: сбор BSI живёт устойчиво,
    # пока не случится вылазка в KWP-блок; после неё возврат на BSI падает и канал
    # рушится - неважно, была вылазка полной или лёгкой. Пока переход KWP -> UDS не
    # разобран, служба занимается тем, что работает надёжно. Включать вручную:
    # --quick-every 60, и лучше при наблюдении за журналом.
    ap.add_argument("--quick-every", type=float, default=0.0,
                    help="как часто делать лёгкий замер (см. QUICK), с; 0 - выключено")
    # Ручной прогон под with-lexia.sh. Тот ставит флаг паузы, чтобы СЛУЖБА
    # отпустила устройство, - но drive.py видит тот же флаг и парковался сам,
    # так что проверить вылазку вручную было нельзя. С этим флагом ручной
    # экземпляр флаг игнорирует и работает, а служба остаётся припаркованной.
    ap.add_argument("--ignore-pause", action="store_true",
                    help="не парковаться по флагу паузы (для ручного прогона под with-lexia.sh)")
    ap.add_argument("--exit-after-idle", type=float, default=0,
                    help="выйти, если устройства нет столько секунд (0 - ждать вечно). "
                         "Нужно для автозапуска по udev: вынул Lexia - служба сама завершилась")
    args = ap.parse_args()

    def pause_requested():
        return (not args.ignore_pause) and os.path.exists(PAUSE_FLAG)

    # force=True обязателен, и без него --log молча не работал.
    #
    # drive.py импортирует ecu, poll_all, telemetry и sync, а каждый из них зовёт
    # logging.basicConfig на уровне модуля - так сделано во всех скриптах проекта.
    # Первый же импорт ставит корневому логгеру StreamHandler, после чего наш
    # basicConfig(handlers=[FileHandler, StreamHandler]) становится пустышкой:
    # basicConfig без force ничего не делает, если обработчики уже есть.
    # Ошибки при этом нет, вывод идёт в консоль в нужном формате, и всё выглядит
    # рабочим - а файл, указанный в --log, остаётся нулевого размера. Именно
    # поэтому drive.log лежал пустой с 16 августа, и журнал службы приходилось
    # читать через journalctl.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(args.log), logging.StreamHandler()],
        force=True)
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

    # Задачи вылазок: блок + набор параметров + интервал. Источник - либо ручной
    # --ecus (целые блоки), либо расписание schedule.py.
    wanted = []
    if args.ecus:
        for tok in args.ecus.split(","):
            tok = tok.strip()
            if not tok:
                continue
            addr, _, secs = tok.partition(":")
            wanted.append(dict(addr=int(addr, 16), every=float(secs) if secs else args.ecu_every,
                               names=None, label=None))
    elif args.ecu_every:
        wanted = list(SCHEDULE)
    tasks = []
    for w in wanted:
        want = w["addr"]
        key = next((k for k in ECUS if k[0] == want), None)
        if key is None:
            log.warning(f"блока 0x{want:03X} нет в каталоге - пропускаю")
            continue
        if want in SKIP_BLOCKS:
            # VCI 0x6C8 воспроизводимо кладёт интерфейс (24 молчащих запроса подряд,
            # следующий блок уже не отвечает). Список один - poll_all.SKIP_BLOCKS.
            log.warning(f"блок 0x{want:03X} в списке пропуска - не трогаю")
            continue
        info = ECUS[key] if w["names"] is None else quick_info(ECUS[key], set(w["names"]))
        label = w["label"] or ECUS[key]["ru"]
        tasks.append(dict(key=key, every=w["every"], info=info, label=label, due=0.0,
                          kind=w.get("kind", "params")))
        if args.ecu_every:
            log.info(f"вылазка 0x{key[0]:03X} {label}: {len(info['params'])} параметров, "
                     f"{len(info.get('requests', []))} запросов, раз в {w['every']:.0f} с")
    extra = {t["key"]: t["every"] for t in tasks}     # для лёгкого замера (QUICK)
    gap = args.ecu_every if args.ecus else (MIN_GAP if args.ecu_every else 0.0)
    if tasks:
        per30 = sum(1800 / t["every"] for t in tasks)
        log.info(f"вылазок за 30 минут по расписанию ~{per30:.0f}, зазор между ними {gap:.0f} с")

    dead_all = load_dead()
    log.info("запросов, помеченных молчащими: "
             + (", ".join(f"{k} {len(v)}" for k, v in sorted(dead_all.items())) or "нет"))

    lex = None
    last_ecu = 0.0
    last_quick = 0.0
    n_ecu = n_quick = 0
    ecu_turn = 0
    last_full = 0.0
    period = 1.0 / args.hot_hz if args.hot_hz > 0 else 0.5
    n_hot = n_full = n_reconnect = 0
    fails = 0
    idle_since = None
    t_report = time.time()

    paused = False
    while not _stop:
        if pause_requested():
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
            # Счётчик простоя обязательно обнулить. Пауза - это НЕ "устройства
            # нет", это "попросили не трогать", и время паузы в простой не идёт.
            # Иначе получается так: до паузы было неудачное подключение и счётчик
            # запустился, потом with-lexia.sh --keep держал флаг девять минут, и
            # служба вышла по --exit-after-idle 90 в первую же секунду после
            # снятия флага. Поднять её обратно может только udev по появлению
            # устройства, а устройство никуда не девалось - то есть сбор встал
            # насовсем. Случилось 01.09.2026.
            idle_since = None
        if lex is None:
            # Первая попытка после запуска - с выдержкой; дальше отступ растёт,
            # чтобы не долбить залипшее устройство раз в три секунды.
            settle = 3.0 if n_reconnect == 0 else 1.0
            lex = connect(settle=settle)
            if lex is None:
                fails += 1
                back = min(3.0 * fails, 20.0)
                # Простой - это не только "разъёма нет". Залипшее устройство
                # открывается, но падает на инициализации, и раньше служба крутилась
                # в этом цикле бесконечно, потому что счётчик простоя не запускался.
                if idle_since is None:
                    idle_since = time.time()
                if args.exit_after_idle and time.time() - idle_since > args.exit_after_idle:
                    log.info(f"устройства нет {args.exit_after_idle:.0f} с - завершаюсь")
                    break
                time.sleep(back)
                continue
            idle_since = None
            fails = 0
            n_reconnect += 1
            log.info(f"связь установлена (подключение №{n_reconnect})")

        t0 = time.time()
        # Вылазка в чужой блок. Делается редко и намеренно: переключение канала
        # плюс чтение занимают несколько секунд, и на это время частый опрос
        # оборотов встаёт. Раз в пять минут дырка в потоке BSI заметно реже
        # самого потока, а данные двигателя того стоят.
        # Лёгкий замер: дешёвый, поэтому часто. Идёт раньше полной вылазки, чтобы
        # не ждать её очереди.
        if (args.quick_every and QUICK and not pause_requested()
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
                    dead = dead_all.setdefault(info["fam"], set())
                    vals, _, _ = poll_ecu(lex, tx, rx, info, dead=dead)
                    store.write([(0, f"{info['fam']}:{n}", u, v)
                                 for n, (v, u) in vals.items()], ts=time.time())
                    n_quick += len(vals)
                except Exception as e:
                    log.warning(f"лёгкий замер {info['ru']} не удался ({type(e).__name__})")
                break          # только первый блок из списка, у которого есть QUICK
            try:
                enter(lex, 0x752, 0x652)
            except Exception as e:
                # Молча тут падать НЕЛЬЗЯ. Раньше эта ветка не писала в журнал, и в
                # логе была просто 69-секундная тишина, а потом переподключение -
                # искать причину было нечем. Возврат на BSI после KWP-блока и есть
                # то место, где рушится канал.
                log.warning(f"возврат на BSI не удался ({type(e).__name__}: {e}) - "
                            f"переподключаюсь")
                try:
                    lex.disconnect()
                except Exception:
                    pass
                lex = None
                continue

        # Проверка флага ещё и здесь: вылазка занимает до 15 с, и если начать её
        # с уже поставленным флагом, тот, кто просит USB, будет ждать всю вылазку.
        due = [t for t in tasks if t0 >= t["due"]]
        if due and gap and t0 - last_ecu >= gap and not pause_requested():
            last_ecu = t0
            # По ОДНОЙ задаче за вылазку - самая просроченная. Обойти все за раз -
            # это минуты, в которые не идёт ничего другого. У каждой задачи свой
            # интервал (schedule.py), поэтому горячие параметры двигателя читаются
            # каждые полторы минуты, а подушки раз в четверть часа.
            task = min(due, key=lambda t: t["due"])
            task["due"] = t0 + task["every"]
            tx, rx = task["key"]
            info = task["info"]
            ecu_turn += 1
            try:
                if task["kind"] == "dtc":
                    n_ecu += read_dtc(lex, tx, rx, info, store)
                else:
                    enter(lex, tx, rx)
                    vals, refused, silent = poll_ecu(lex, tx, rx, info)
                    store.write([(0, f"{info['fam']}:{n}", u, v)
                                 for n, (v, u) in vals.items()], ts=time.time())
                    n_ecu += len(vals)
                    log.info(f"снимок {task['label']}: {len(vals)} значений "
                             f"(отказ {refused}, молчание {silent})")
            except usb.core.USBError as e:
                # Устройство залипло: дальше сорвётся и обычный опрос BSI, поэтому
                # переподключаемся, а вылазки на этот сеанс прекращаем.
                log.warning(f"интерфейс перестал отвечать на {task['label']} "
                            f"({type(e).__name__}) - вылазки отключены до перезапуска")
                tasks = []
                extra = {}
                try:
                    lex.disconnect()
                except Exception:
                    pass
                lex = None
                continue
            except Exception as e:
                # Блок не ответил - выкидываем только его задачи, остальные не виноваты.
                log.warning(f"снимок {task['label']} не удался ({type(e).__name__}: {e}) - "
                            f"этот блок больше не трогаю")
                tasks = [t for t in tasks if t["key"] != (tx, rx)]
                extra.pop((tx, rx), None)
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
