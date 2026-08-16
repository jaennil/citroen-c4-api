# C4 CAN Bus - управление фарами Citroen C4

## Требования

- ELM327 (Bluetooth или USB)
- Python 3 + venv

## Установка

```bash
cd /home/jaennil/projects/c4-can
source .venv/bin/activate
```

## Подключение ELM327 Bluetooth

```bash
# Включить Bluetooth
bluetoothctl power on
bluetoothctl scan on
# Найти MAC адрес ELM327 (обычно OBDII или похожее название)
bluetoothctl pair XX:XX:XX:XX:XX:XX
bluetoothctl trust XX:XX:XX:XX:XX:XX

# Привязать serial порт
sudo rfcomm bind 0 XX:XX:XX:XX:XX:XX
# Появится /dev/rfcomm0
```

Нужен пакет `bluez-deprecated-tools` для rfcomm:
```bash
sudo pacman -S bluez-deprecated-tools
```

## Шаги

### 1. Проверка адаптера
```bash
python 01_test_elm.py /dev/rfcomm0
```

### 2. Сниффинг CAN
```bash
# Сначала сними дамп с выключенными фарами (10 сек, Ctrl+C)
python 02_sniff_can.py /dev/rfcomm0
mv can_dump.log dump_off.log

# Потом с включёнными фарами (10 сек, Ctrl+C)
python 02_sniff_can.py /dev/rfcomm0
mv can_dump.log dump_on.log
```

### 3. Анализ
```bash
python 03_analyze_dump.py dump_off.log dump_on.log
```
