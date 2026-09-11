#!/usr/bin/env bash
# Публиковать имя c4.local через mDNS на текущий IPv4 ноутбука, чтобы с телефона в
# хотспоте открывалось http://c4.local без запоминания IP и порта. avahi-daemon
# публикует только собственное имя хоста (jaennil-honor.local), псевдоним даёт
# avahi-publish, и его надо перезапускать при смене адреса - хотспот раздаёт разные.
set -uo pipefail
IFACE="${IFACE:-wlp2s0}"
NAME="${NAME:-c4.local}"
pid=""; cur=""
stop() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; exit 0; }
trap stop TERM INT
while true; do
  ip=$(ip -4 -o addr show "$IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
  if [ -n "$ip" ] && { [ "$ip" != "$cur" ] || ! kill -0 "$pid" 2>/dev/null; }; then
    [ -n "$pid" ] && kill "$pid" 2>/dev/null
    avahi-publish -a -R "$NAME" "$ip" >/dev/null 2>&1 &
    pid=$!; cur="$ip"; echo "$NAME -> $ip"
  elif [ -z "$ip" ] && [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null; pid=""; cur=""; echo "нет адреса на $IFACE"
  fi
  sleep 10 & wait $!
done
