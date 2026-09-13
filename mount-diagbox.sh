#!/usr/bin/env bash
# Смонтировать образ DiagBox только на чтение и достать из него таблицу строк.
#
# Зачем. В каталоге параметров (ecu_groups_jsons) описания лежат НЕ текстом, а
# ссылками вида @P8639 - индексами в таблицу строк DiagBox. Пока таблицы нет,
# у нас 54 описания кодов, вытащенных грубым сканированием strings по всему
# образу, и расшифровки состояний параметров в виде голых номеров ("00" -> "11643").
# Найдём таблицу - получим и описания всех кодов, и тексты всех состояний.
#
# Образ - разреженный VMDK, обычным loop его не примонтировать, поэтому qemu-nbd.
#
#     sudo ./mount-diagbox.sh          # смонтировать в /mnt/diagbox
#     sudo ./mount-diagbox.sh --umount # отцепить
#
set -uo pipefail
MNT="${MNT:-/mnt/diagbox}"
DEV=/dev/nbd0
RUN_AS="${SUDO_USER:-jaennil}"
# ДОМАШНИЙ КАТАЛОГ ВЛАДЕЛЬЦА, не root: под sudo $HOME становится /root, и скрипт
# искал образ в /root/Downloads. Берём каталог того, кто запустил sudo.
HOME_DIR=$(getent passwd "$RUN_AS" | cut -d: -f6)
IMG="${IMG:-$HOME_DIR/Downloads/DiagBox_968_Free/Diagbox_968_Free.vmdk}"

[ "$(id -u)" = 0 ] || { echo "нужен root: sudo $0"; exit 1; }

if [ "${1:-}" = "--umount" ]; then
  umount "$MNT" 2>/dev/null
  qemu-nbd -d "$DEV" >/dev/null 2>&1
  echo "отцеплено"; exit 0
fi

[ -f "$IMG" ] || { echo "нет образа: $IMG"; exit 1; }
modprobe nbd max_part=16 || { echo "не загрузился модуль nbd"; exit 1; }
qemu-nbd -d "$DEV" >/dev/null 2>&1
# ТОЛЬКО ЧТЕНИЕ: -r. Образ чужой и с снапшотом (.REDO), писать в него нельзя.
qemu-nbd -r -c "$DEV" "$IMG" || { echo "qemu-nbd не смог подключить образ"; exit 1; }
sleep 2
partprobe "$DEV" 2>/dev/null
lsblk -no NAME,SIZE,FSTYPE "$DEV" | sed 's/^/  /'

# Системный раздел - самый большой NTFS. Первый (100 МБ) это загрузчик Windows.
PART=$(lsblk -rno NAME,SIZE,FSTYPE "$DEV" | awk '$3=="ntfs"{print $1, $2}' | sort -k2 -h | tail -1 | cut -d' ' -f1)
[ -n "$PART" ] || { echo "не нашёл NTFS-раздел"; qemu-nbd -d "$DEV"; exit 1; }
mkdir -p "$MNT"
mount -o ro,uid="$(id -u "$RUN_AS")" "/dev/$PART" "$MNT" || {
  echo "не смонтировался /dev/$PART"; qemu-nbd -d "$DEV"; exit 1; }
echo "смонтировано: /dev/$PART -> $MNT (только чтение)"
ls "$MNT" | head
