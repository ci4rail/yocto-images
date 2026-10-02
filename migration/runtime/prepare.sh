#!/bin/sh
. /migration/common.sh
trap cleanup_mounts EXIT
check_platform
identify_emmc
unmounted_emmc
[ ! -e "$BACKUP" ] && [ ! -e "$STATE/prepared" ] || fail 'A previous backup or installation exists; do not retry blindly'
sectors=$(cat "/sys/class/block/$DISK/size")
[ "$sectors" -ge $((MINIMUM_DISK_MIB * 2048)) ] || fail 'eMMC too small for target layout'
mkdir -p "$OLD" "$NEW"
printf '%s  %s\n' "$PAYLOAD_CHECKSUM_SHA256" /migration/image/SHA256SUMS |
    sha256sum -c - || fail 'Payload checksum manifest changed'
(cd /migration/image && sha256sum -c SHA256SUMS) || fail 'Payload checksum failed'

# Probe only ext filesystems on this eMMC, read-only without journal replay.
# A separate legacy /data partition contains mender/ at its root.
# Refuse multiple candidates instead of guessing which identity to retain.
candidate=
for partition in "${DEVICE}"p*; do
    [ -b "$partition" ] || continue
    type=$(blkid -s TYPE -o value "$partition" || :)
    case "$type" in ext3|ext4) ;; *) continue ;; esac
    mount -t "$type" -o ro,noload "$partition" "$OLD" || fail "Cannot inspect $partition"
    if [ -d "$OLD/mender" ] && [ ! -L "$OLD/mender" ]; then
        [ -z "$candidate" ] || fail 'Multiple legacy Mender directories found'
        candidate=$partition
    fi
    umount "$OLD" || fail 'Cannot unmount legacy partition'
done
[ -n "$candidate" ] || fail 'No separate legacy data partition containing /mender found'
# Repair only the uniquely identified, unmounted legacy data filesystem.
# A forced check is required: journal replay alone can leave a corrupt orphan
# list even when e2fsck -p reports the filesystem clean. Exit 1 means errors
# were corrected; all other nonzero results stop installation.
check_status=0
e2fsck -fn "$candidate" >"$STATE/legacy-fsck.log" 2>&1 || check_status=$?
case "$check_status" in
    0) ;;
    4)
    echo "Migration: Repairing legacy data filesystem $candidate" >/dev/console
    repair_status=0
    e2fsck -fy "$candidate" >"$STATE/legacy-fsck-repair.log" 2>&1 || repair_status=$?
    case "$repair_status" in
        0|1) ;;
        *) fail "Legacy data repair failed (e2fsck exit $repair_status); see $STATE/legacy-fsck-repair.log" ;;
    esac
    e2fsck -fn "$candidate" >"$STATE/legacy-fsck-verify.log" 2>&1 ||
        fail "Legacy data filesystem still has errors; see $STATE/legacy-fsck-verify.log"
    ;;
    *) fail "Legacy data check failed (e2fsck exit $check_status); see $STATE/legacy-fsck.log" ;;
esac
mount -o ro,noload "$candidate" "$OLD" || fail 'Cannot mount legacy data'

# GNU tar preserves ownership, modes, hard links, symlinks, ACLs and xattrs.
# Bound the backup tmpfs independently of the rest of the installation process.
size_kib=$(du -sk "$OLD/mender" | awk '{print $1}')
available_kib=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
limit_kib=$((MAX_BACKUP_MIB * 1024))
[ "$DATA_FREE_KIB" -gt $((size_kib + 16384)) ] || fail 'New data filesystem has insufficient free space for Mender state'
[ "$size_kib" -lt "$limit_kib" ] || fail 'Mender data exceeds backup budget'
[ "$available_kib" -gt $((limit_kib + 262144)) ] || fail 'Insufficient free RAM for backup and flashing'
mkdir -p "$STATE/backup"
mount -t tmpfs -o "size=${MAX_BACKUP_MIB}m,mode=0700" tmpfs "$STATE/backup"
BACKUP=$STATE/backup/mender.tar
[ ! -e "$BACKUP" ] || fail 'Backup already exists'
tar --acls --xattrs --xattrs-include='*' --numeric-owner -cpf "$BACKUP" -C "$OLD" mender || fail 'Backup failed'
tar --acls --xattrs --xattrs-include='*' --numeric-owner -df "$BACKUP" -C "$OLD" || fail 'Backup verification failed'
(cd "$STATE/backup" && sha256sum mender.tar > SHA256SUMS)
printf '%s\n' "$DEVICE" > "$STATE/device"
# Record the eMMC identity as well as the kernel-assigned device name.
cat "/sys/class/block/$DISK/device/cid" > "$STATE/cid"
umount "$OLD" || fail 'Cannot unmount legacy data'
unmounted_emmc
printf '%s\n' ready > "$STATE/prepared"
echo 'Migration: Mender backup verified; installing new partition layout.' >/dev/console
