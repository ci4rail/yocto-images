#!/bin/sh
# Replaces ALL contents of the explicitly selected data partition.
. /migration/common.sh
[ "$#" -eq 2 ] || fail "Usage: $0 SD_BACKUP_FOLDER EMMC_DATA_PARTITION"
check_platform
backup_folder "$1"
select_data_partition "$2"
[ -f "$FOLDER/complete" ] || fail 'Backup is incomplete'
[ "$(cat "$FOLDER/emmc-cid")" = "$(cat "/sys/class/block/$DISK/device/cid")" ] || fail 'Backup belongs to a different eMMC'
(cd "$FOLDER" && sha256sum -c SHA256SUMS) || fail 'Backup checksum failed'
cat "$FOLDER"/data.tar.part-* | "$TAR" -tf - >/dev/null || fail 'Invalid backup archive'
check_data_filesystem
mkdir -p "$NEW"
trap cleanup_mounts EXIT
mount -t "$TYPE" "$PARTITION" "$NEW"
required=$(cat "$FOLDER/required-kib")
case "$required" in ''|*[!0-9]*) fail 'Invalid backup size' ;; esac
# Include currently occupied files, since these are replaced below.
available=$(df -Pk "$NEW" | awk 'END {print $4}')
reclaimable=$(du -sk "$NEW" | awk '{print $1}')
[ $((available + reclaimable)) -gt $((required + 16384)) ] ||
    fail 'Data filesystem is too small; enlarge it before restoring'
# Includes dotfiles. Never follows symlinks or descends into other filesystems.
find "$NEW" -xdev -mindepth 1 -delete
cat "$FOLDER"/data.tar.part-* | "$TAR" --acls --xattrs --xattrs-include='*' \
    --numeric-owner --same-owner --sparse -xpf - -C "$NEW" || fail 'Restore failed; keep the SD backup'
sync
umount "$NEW"
mount -t "$TYPE" -o ro,noload "$PARTITION" "$NEW"
cat "$FOLDER"/data.tar.part-* | "$TAR" --acls --xattrs --xattrs-include='*' \
    --numeric-owner -df - -C "$NEW" || fail 'Restore verification failed'
umount "$NEW"
echo 'Complete /data restored and verified. No reboot performed.'
