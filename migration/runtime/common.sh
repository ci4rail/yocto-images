#!/bin/sh
# Shared guards for manual data backup and restore.
set -eu
umask 077
. /migration/profile.conf
STATE=/run/migration
OLD=$STATE/old
NEW=$STATE/new
MEDIA=/run/media/migration-sd
TAR=/bin/tar.tar

fail() {
    echo "Migration: $*" >&2
    echo "Migration: $*" >/dev/console
    exit 1
}

# Use the TEZI eMMC alias, never a guessed mmcblk number (or the USB stick).
identify_emmc() {
    DEVICE=$(readlink -f /dev/emmc)
    [ -b "$DEVICE" ] || fail 'No eMMC device'
    DISK=${DEVICE##*/}
    case "$DISK" in mmcblk[0-9]*) ;; *) fail 'Unexpected eMMC device' ;; esac
    [ "$(cat "/sys/class/block/$DISK/device/type")" = MMC ] || fail 'Target is not MMC'
    [ "$(cat "/sys/class/block/$DISK/removable")" = 0 ] || fail 'Target is removable'
    [ "$(readlink -f /dev/emmc-boot0)" = "${DEVICE}boot0" ] || fail 'eMMC boot0 mismatch'
}

unmounted_emmc() {
    # Compare major:minor identities, so aliases and bind mounts cannot hide a mount.
    for entry in "/sys/class/block/$DISK" "/sys/class/block/$DISK"p*; do
        [ -f "$entry/dev" ] || continue
        identity=$(cat "$entry/dev")
        if awk -v dev="$identity" '$3 == dev { found=1 } END { exit !found }' /proc/self/mountinfo; then
            fail "Target is mounted: ${entry##*/}"
        fi
    done
    [ "$(wc -l < /proc/swaps)" -eq 1 ] || fail 'Swap must be disabled'
}

check_platform() {
    tr '\000' '\n' </proc/device-tree/compatible | grep -Fx "$SOC" >/dev/null || fail 'Wrong platform'
    # Refuse invocation from a regular installed OS; manual tools require our RAM root.
    awk '$2 == "/" && $3 == "squashfs" { found=1 } END { exit !found }' /proc/mounts || fail 'Root must be the migration squashfs'
}

cleanup_mounts() {
    mountpoint -q "$OLD" && umount "$OLD" || :
    mountpoint -q "$NEW" && umount "$NEW" || :
}

# Require the operator to name the data partition; never guess from old layouts.
select_data_partition() {
    identify_emmc
    unmounted_emmc
    PARTITION=$(readlink -f "$1")
    case "$PARTITION" in
        "${DEVICE}"p*) suffix=${PARTITION#"${DEVICE}"p} ;;
        *) fail 'Data partition must belong to eMMC' ;;
    esac
    case "$suffix" in ''|*[!0-9]*) fail 'Invalid partition number' ;; esac
    [ -b "$PARTITION" ] || fail 'Data partition missing'
    TYPE=$(blkid -s TYPE -o value "$PARTITION")
    case "$TYPE" in ext3|ext4) ;; *) fail 'Expected an ext3/ext4 data partition' ;; esac
}

backup_folder() {
    mountpoint -q "$MEDIA" || fail 'SD card is not mounted'
    parent=$(dirname "$1")
    [ -d "$parent" ] || fail 'Create the backup parent directory first'
    parent=$(readlink -f "$parent")
    FOLDER=$parent/$(basename "$1")
    [ ! -L "$FOLDER" ] || fail 'Backup folder must not be a symlink'
    case "$(basename "$1")" in .|..) fail 'Invalid backup folder' ;; esac
    case "$FOLDER" in "$MEDIA"/*) ;; *) fail "Backup folder must be below $MEDIA" ;; esac
}

check_data_filesystem() {
    mkdir -p "$STATE"
    e2fsck -fn "$PARTITION" >"$STATE/data-fsck.log" 2>&1 ||
        fail "Data filesystem needs attention; see $STATE/data-fsck.log"
}
