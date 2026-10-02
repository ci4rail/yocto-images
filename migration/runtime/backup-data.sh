#!/bin/sh
# Usage: backup-data.sh /run/media/migration-sd/backups/DEVICE /dev/emmc-partition
. /migration/common.sh
[ "$#" -eq 2 ] || fail "Usage: $0 SD_BACKUP_FOLDER EMMC_DATA_PARTITION"
check_platform
backup_folder "$1"
select_data_partition "$2"
[ ! -e "$FOLDER" ] || fail 'Backup folder already exists; choose a new folder'
check_data_filesystem
mkdir -p "$OLD"
trap cleanup_mounts EXIT
mount -t "$TYPE" -o ro,noload "$PARTITION" "$OLD"
mkdir -p "$FOLDER"
# A FIFO lets us check both tar and split, without relying on shell pipefail.
fifo=$STATE/backup.pipe
mkfifo "$fifo"
trap 'rm -f "$fifo"; cleanup_mounts' EXIT
/migration/split-backup "$FOLDER/data.tar.part-" < "$fifo" &
split_pid=$!
tar_status=0
"$TAR" --acls --xattrs --xattrs-include='*' --numeric-owner --sparse \
    -cpf "$fifo" -C "$OLD" . || tar_status=$?
split_status=0
wait "$split_pid" || split_status=$?
[ "$tar_status" -eq 0 ] && [ "$split_status" -eq 0 ] || fail 'Backup incomplete; choose a new folder for retry'
cat "$FOLDER"/data.tar.part-* | "$TAR" --acls --xattrs --xattrs-include='*' \
    --numeric-owner -df - -C "$OLD" || fail 'Backup verification failed'
du -sk "$OLD" | awk '{print $1}' > "$FOLDER/required-kib"
cat "/sys/class/block/$DISK/device/cid" > "$FOLDER/emmc-cid"
printf '%s\n' "$PARTITION" > "$FOLDER/source-partition"
(cd "$FOLDER" && sha256sum data.tar.part-* required-kib emmc-cid source-partition > SHA256SUMS)
sync
# Restore only accepts a fully written, verified backup.
touch "$FOLDER/complete"
sync
echo "Complete /data backup saved to $FOLDER"
