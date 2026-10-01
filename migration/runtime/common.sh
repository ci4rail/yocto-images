#!/bin/sh
# The immutable profile and payload are created by migration/build.py.
set -eu
umask 077
. /migration/profile.conf
STATE=/run/migration
OLD=$STATE/old
NEW=$STATE/new
BACKUP=$STATE/backup/mender.tar

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
    # Refuse invocation from a regular installed OS; hooks require our RAM root.
    awk '$2 == "/" && $3 == "squashfs" { found=1 } END { exit !found }' /proc/mounts || fail 'Root must be the migration squashfs'
    [ -f /migration/image/SHA256SUMS ] || fail 'Embedded payload missing'
}

cleanup_mounts() {
    mountpoint -q "$OLD" && umount "$OLD" || :
    mountpoint -q "$NEW" && umount "$NEW" || :
}
