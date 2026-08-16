"""
Живой дашборд Citroen C4 через OBD2.
Показывает обороты, скорость, температуру в реальном времени.
"""

import serial
import time
import sys
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
ser = serial.Serial(PORT, 38400, timeout=3)


def cmd(c, t=3):
    ser.flushInput()
    time.sleep(0.05)
    ser.flushInput()
    ser.write(f"{c}\r".encode())
    resp = b""
    deadline = time.time() + t
    while time.time() < deadline:
        if ser.in_waiting:
            resp += ser.read(ser.in_waiting)
            time.sleep(0.02)
        else:
            if b">" in resp:
                break
            time.sleep(0.05)
    time.sleep(0.05)
    ser.flushInput()
    return resp.decode("ascii", errors="ignore").strip()


def parse_obd(resp, pid):
    """Извлечь байты данных из OBD ответа для конкретного PID."""
    parts = resp.replace("\r", " ").replace("\n", " ").replace(">", "").strip().split()
    # Ищем "41 PID" и берём данные после
    pid_hex = f"{pid:02X}"
    data_bytes = []
    try:
        for i in range(len(parts) - 1):
            if parts[i] == "41" and parts[i + 1].upper() == pid_hex:
                data_bytes = [int(x, 16) for x in parts[i + 2:] if len(x) == 2]
                break
    except (ValueError, IndexError):
        pass
    return data_bytes


def get_rpm():
    resp = cmd("010C", 2)
    data = parse_obd(resp, 0x0C)
    if len(data) >= 2:
        return (data[0] * 256 + data[1]) / 4
    return None


def get_speed():
    resp = cmd("010D", 2)
    data = parse_obd(resp, 0x0D)
    if len(data) >= 1:
        return data[0]
    return None


def get_coolant_temp():
    resp = cmd("0105", 2)
    data = parse_obd(resp, 0x05)
    if len(data) >= 1:
        return data[0] - 40
    return None


def get_intake_temp():
    resp = cmd("010F", 2)
    data = parse_obd(resp, 0x0F)
    if len(data) >= 1:
        return data[0] - 40
    return None


def get_throttle():
    resp = cmd("0111", 2)
    data = parse_obd(resp, 0x11)
    if len(data) >= 1:
        return round(data[0] * 100 / 255, 1)
    return None


def get_engine_load():
    resp = cmd("0104", 2)
    data = parse_obd(resp, 0x04)
    if len(data) >= 1:
        return round(data[0] * 100 / 255, 1)
    return None


def get_voltage():
    resp = cmd("ATRV", 2)
    for part in resp.split():
        if "V" in part:
            try:
                return float(part.replace("V", ""))
            except ValueError:
                pass
    return None


def get_timing_advance():
    resp = cmd("010E", 2)
    data = parse_obd(resp, 0x0E)
    if len(data) >= 1:
        return data[0] / 2 - 64
    return None


def get_fuel_trim_short():
    resp = cmd("0106", 2)
    data = parse_obd(resp, 0x06)
    if len(data) >= 1:
        return round((data[0] - 128) * 100 / 128, 1)
    return None


def get_fuel_trim_long():
    resp = cmd("0107", 2)
    data = parse_obd(resp, 0x07)
    if len(data) >= 1:
        return round((data[0] - 128) * 100 / 128, 1)
    return None


def main():
    # Инициализация
    cmd("ATZ", 3)
    cmd("ATE0")
    cmd("ATH0")
    cmd("ATSP6")

    log.info("╔══════════════════════════════════════╗")
    log.info("║     CITROEN C4 LIVE DASHBOARD        ║")
    log.info("║     Ctrl+C чтобы остановить          ║")
    log.info("╚══════════════════════════════════════╝\n")

    try:
        while True:
            rpm = get_rpm()
            speed = get_speed()
            coolant = get_coolant_temp()
            intake = get_intake_temp()
            throttle = get_throttle()
            load = get_engine_load()
            voltage = get_voltage()
            timing = get_timing_advance()
            stft = get_fuel_trim_short()
            ltft = get_fuel_trim_long()

            # Очищаем экран
            os.system("clear")

            print("╔══════════════════════════════════════╗")
            print("║     CITROEN C4 LIVE DASHBOARD        ║")
            print("╠══════════════════════════════════════╣")

            if rpm is not None:
                bar = "█" * int(rpm / 200)
                print(f"║  Обороты:    {rpm:6.0f} RPM  {bar}")

            if speed is not None:
                print(f"║  Скорость:   {speed:6d} км/ч")

            if coolant is not None:
                print(f"║  Т двигателя:{coolant:5d}°C")

            if intake is not None:
                print(f"║  Т впуска:   {intake:5d}°C")

            if throttle is not None:
                print(f"║  Дроссель:   {throttle:5.1f}%")

            if load is not None:
                print(f"║  Нагрузка:   {load:5.1f}%")

            if timing is not None:
                warn = " ⚠ LIMP!" if timing < 5 else ""
                print(f"║  Опережение: {timing:5.1f}°{warn}")

            if stft is not None:
                print(f"║  Коррекция:  {stft:+5.1f}% (кратк)")

            if ltft is not None:
                print(f"║  Коррекция:  {ltft:+5.1f}% (долг)")

            if voltage is not None:
                print(f"║  Напряжение: {voltage:5.1f}V")

            print("╚══════════════════════════════════════╝")

    except KeyboardInterrupt:
        ser.close()
        print("\nВыход.")


if __name__ == "__main__":
    main()
