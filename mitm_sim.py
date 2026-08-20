"""
Проверка логики перехвата на виртуальной шине CAN - без машины и без Teensy.

Две части:

  * ПРОВЕРКИ (--test): каждое из четырёх правил проверяется отдельно, синтетическими
    кадрами. Это то, что нельзя отлаживать в машине: ошибка означает пропавшие на
    ходу поворотники, потому что они едут в том же кадре, что и свет.
  * ПРОГОН (--run): та же логика на настоящей шине python-can типа "virtual" -
    два конца, между ними мост. Структурно это репетиция того, что будет делать
    Teensy: слушать, по команде размыкать реле, дописывать бит и отдавать дальше.

Виртуальная шина выбрана намеренно: ей не нужен ни root, ни модуль ядра, поэтому
прогон работает на любом ноутбуке. Когда появится железо, тот же сценарий можно
повторить на vcan0 через SocketCAN, поменяв одну строку.

Чего эта проверка НЕ доказывает, и это важно: какой бит в кадре отвечает за дальний
свет. Номер взят из описания COM2008P и на этой машине не измерен. Симулятор
проверяет логику вокруг бита, а сам бит подтверждается только замером на шине.

    ./.venv/bin/python mitm_sim.py --test
    ./.venv/bin/python mitm_sim.py --run
"""

import argparse
import sys
import time

from mitm_logic import HIGH_BEAM_BIT, STALK_ID, Mitm


def frame(high_beam=False, blink=False, wipers=0):
    """Синтетический кадр подрулевого.

    Кроме дальнего сюда намеренно положены другие функции: смысл правила
    прозрачности в том, что они обязаны доходить неизменными.
    """
    b0 = 0
    if high_beam:
        b0 |= 1 << HIGH_BEAM_BIT
    if blink:
        b0 |= 1 << 3
    return bytes([b0, wipers & 0xFF, 0, 0, 0, 0, 0, 0])


# --- проверки -------------------------------------------------------------

def check(name, cond, detail=""):
    print(f"  [{'ок ' if cond else 'ПЛОХО'}] {name}" + (f" - {detail}" if detail else ""))
    return cond


def test_default_transparent():
    """Правило 4: без команды кадр не переписывается и мы молчим."""
    m = Mitm()
    out, tx = m.on_frame(STALK_ID, frame(), 0.0)
    return (check("по умолчанию прозрачно: не передаём", tx is False)
            and check("реле замкнуто", m.relay_open is False))


def test_no_tx_while_closed():
    """Правило 1: пока реле замкнуто, передача запрещена."""
    m = Mitm()
    m.relay_open = False
    m.force = True                      # даже если форсирование почему-то стоит
    out, tx = m.on_frame(STALK_ID, frame(), 0.0)
    return check("реле замкнуто - не передаём даже при форсировании", tx is False)


def test_force_sets_bit():
    """Команда поднимает бит и не портит остальное содержимое кадра."""
    m = Mitm()
    m.command(True, 0.0)
    src = frame(high_beam=False, blink=True, wipers=0x25)
    out, tx = m.on_frame(STALK_ID, src, 0.1)
    return (check("передаём при разомкнутом реле", tx is True)
            and check("бит дальнего поднят", bool(out[0] & (1 << HIGH_BEAM_BIT)))
            and check("моргание не тронуто", bool(out[0] & (1 << 3)))
            and check("стеклоочистители не тронуты", out[1] == 0x25)
            and check("остальные байты не тронуты", out[2:] == src[2:]))


def test_stalk_wins():
    """Правило 2: движение рычага снимает форсирование немедленно."""
    m = Mitm()
    m.command(True, 0.0)
    m.on_frame(STALK_ID, frame(high_beam=False), 0.1)      # запомнить положение
    out, tx = m.on_frame(STALK_ID, frame(high_beam=True), 0.2)   # водитель тронул
    return (check("форсирование снято рычагом", m.force is False)
            and check("реле вернулось в замкнутое", m.relay_open is False)
            and check("после снятия не передаём", tx is False))


def test_heartbeat_timeout():
    """Правило 3: пропал пульс - возвращаемся в прозрачный режим."""
    m = Mitm(timeout=3.0)
    m.command(True, 100.0)
    m.on_frame(STALK_ID, frame(), 100.1)
    m.heartbeat(101.0)
    m.tick(103.5)
    still = m.force
    m.tick(105.0)
    return (check("в пределах таймаута форсирование держится", still is True)
            and check("после тишины форсирование снято", m.force is False)
            and check("реле вернулось в замкнутое", m.relay_open is False))


def test_foreign_frames_untouched():
    """Чужие идентификаторы логику не трогают."""
    m = Mitm()
    m.command(True, 0.0)
    out, tx = m.on_frame(0x128, frame(high_beam=False), 0.1)
    return (check("чужой кадр не передаём", tx is False)
            and check("чужой кадр не переписан", out[0] == 0))


def run_tests():
    tests = [
        ("прозрачность по умолчанию", test_default_transparent),
        ("запрет передачи при замкнутом реле", test_no_tx_while_closed),
        ("форсирование поднимает бит, остальное цело", test_force_sets_bit),
        ("рычаг главнее команды", test_stalk_wins),
        ("автоотключение по тишине", test_heartbeat_timeout),
        ("чужие кадры не трогаются", test_foreign_frames_untouched),
    ]
    ok = True
    for name, fn in tests:
        print(f"\n{name}:")
        ok &= bool(fn())
    print("\n" + ("все правила соблюдены" if ok else "ЕСТЬ НАРУШЕНИЯ - в машину нельзя"))
    return 0 if ok else 1


# --- прогон на виртуальной шине -------------------------------------------

def run_bus():
    import can

    # Две шины с одним каналом - это и есть "провод": что послал один конец,
    # видят остальные. Роль подрулевого играет генератор, роль BSI - слушатель.
    stalk = can.Bus(interface="virtual", channel="mitm")
    bsi = can.Bus(interface="virtual", channel="mitm")
    m = Mitm()
    print("прогон на виртуальной шине; кадр подрулевого 0x094, бит дальнего", HIGH_BEAM_BIT)
    print("сценарий: 3 кадра прозрачно, команда, 3 кадра с подменой, рычаг перебивает\n")

    t = 0.0
    plan = [("прозрачно", None), ("прозрачно", None), ("прозрачно", None),
            ("команда R1", "on"), ("подмена", None), ("подмена", None),
            ("рычаг тронут", "stalk"), ("после рычага", None)]
    for label, event in plan:
        t += 0.1
        if event == "on":
            m.command(True, t)
        stalk_high = event == "stalk"
        msg = can.Message(arbitration_id=STALK_ID, data=frame(high_beam=stalk_high),
                          is_extended_id=False)
        stalk.send(msg)
        got = bsi.recv(timeout=0.2)
        out, tx = m.on_frame(got.arbitration_id, bytes(got.data), t)
        st = m.state()
        print(f"  {label:<14} вход {got.data[0]:08b} -> "
              f"{'передаём ' + format(out[0], '08b') if tx else 'молчим':<20} "
              f"реле {'разомкнуто' if st['relay_open'] else 'замкнуто':<10} "
              f"форсирование {'да' if st['force'] else 'нет'}")
    stalk.shutdown()
    bsi.shutdown()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="проверить четыре правила")
    ap.add_argument("--run", action="store_true", help="прогон на виртуальной шине")
    args = ap.parse_args()
    if not args.test and not args.run:
        ap.error("укажи --test или --run")
    rc = 0
    if args.test:
        rc |= run_tests()
    if args.run:
        rc |= run_bus()
    return rc


if __name__ == "__main__":
    sys.exit(main())
