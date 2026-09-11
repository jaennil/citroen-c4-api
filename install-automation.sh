#!/usr/bin/env bash
# Установка автоматики: воткнул Lexia - сбор пошёл, вернулся домой - данные уехали.
# Запускать через sudo: sudo ./install-automation.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ "$(id -u)" = 0 ] || { echo "нужен root: sudo $0"; exit 1; }

install -m 644 "$HERE/systemd/c4-telemetry.service" /etc/systemd/system/
install -m 644 "$HERE/systemd/c4-sync.service"      /etc/systemd/system/
install -m 644 "$HERE/systemd/c4-sync-live.service" /etc/systemd/system/
install -m 644 "$HERE/systemd/c4-sync-local.service" /etc/systemd/system/
install -m 644 "$HERE/systemd/c4-mdns.service"       /etc/systemd/system/
install -m 644 "$HERE/systemd/c4-sync.timer"        /etc/systemd/system/
install -m 644 "$HERE/70-psa-diag.rules"            /etc/udev/rules.d/

systemctl daemon-reload
systemctl enable --now c4-sync.timer
# имя c4.local для локальной Grafana: avahi публикует, c4-mdns следит за адресом
systemctl enable --now avahi-daemon.service
systemctl enable --now c4-mdns.service
udevadm control --reload
udevadm trigger --subsystem-match=usb --attr-match=idVendor=103a

echo
echo "Готово. Проверить:"
echo "  systemctl status c4-telemetry     # сбор (стартует при подключении Lexia)"
echo "  systemctl list-timers c4-sync     # досылка раз в 15 минут"
echo "  journalctl -u c4-telemetry -f     # живой лог сбора"
echo "  http://c4.local                  # локальная Grafana (local/up.sh)"
