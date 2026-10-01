#!/bin/sh
. /migration/common.sh
trap cleanup_mounts EXIT
identify_emmc
unmounted_emmc
[ -f "$STATE/prepared" ] || fail 'Verified backup marker missing'
[ "$(cat "$STATE/device")" = "$DEVICE" ] || fail 'Target changed during installation'
[ "$(cat "$STATE/cid")" = "$(cat "/sys/class/block/$DISK/device/cid")" ] || fail 'eMMC identity changed'
(cd "$STATE/backup" && sha256sum -c SHA256SUMS) || fail 'Backup checksum failed; DO NOT REBOOT'
partition=${DEVICE}p4
[ "$(blkid -s TYPE -o value "$partition")" = ext4 ] || fail 'New data partition is not ext4'
mount -t ext4 "$partition" "$NEW" || fail 'Cannot mount new data partition'
[ ! -L "$NEW/mender" ] || fail 'New Mender directory is a symlink'
# Replace the image's initial Mender state rather than merging two identities.
rm -rf "$NEW/mender"
tar --acls --xattrs --xattrs-include='*' --numeric-owner -xpf "$STATE/backup/mender.tar" -C "$NEW" || fail 'Restore failed; DO NOT REBOOT'
sync
umount "$NEW" || fail 'Cannot flush restored data'
# Reopen read-only and compare the persisted files with the backup.
mount -t ext4 -o ro,noload "$partition" "$NEW" || fail 'Cannot verify restored data'
tar --acls --xattrs --xattrs-include='*' --numeric-owner -df "$STATE/backup/mender.tar" -C "$NEW" || fail 'Restore verification failed; DO NOT REBOOT'
umount "$NEW" || fail 'Cannot unmount restored data'
printf '%s\n' restored > "$STATE/restored"
sync
# Do not reboot automatically; keep backup/logs available for the operator.
