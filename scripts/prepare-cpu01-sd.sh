#!/usr/bin/env bash
# Prepare a CPU01 recovery SD card over SSH from the build host.
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: prepare-cpu01-recovery-sd.sh [--images DIR] [--check] [--yes] TARGET_IP

Transfers the CPU01 standard-image rootfs/data filesystems, erases the target's
/dev/mmcblk1 SD card, and installs/adapts the recovery copy. Run while the target
is booted from eMMC. It does not reboot or change U-Boot settings.

  --images DIR  Deploy directory (default: repository's
                cpu01-standard-image/install/images/moducop-cpu01)
  --check       Check images and target prerequisites only; no writes or transfer
  --yes         Skip the interactive SD erasure confirmation
  -h, --help    Show this help

Uses root SSH login and legacy scp (-O). SSH handles password/key authentication
and host-key verification normally; passwords are not stored by this script.
USAGE
}
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
images="$repo_dir/cpu01-standard-image/install/images/moducop-cpu01"
check_only=0
assume_yes=0
target_ip=
while (($#)); do
    case "$1" in
        --images) (($# >= 2)) || fail '--images needs a directory'; images=$2; shift 2 ;;
        --check) check_only=1; shift ;;
        --yes) assume_yes=1; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) fail "Unknown option: $1" ;;
        *) [[ -z "$target_ip" ]] || fail 'Specify exactly one target IP'; target_ip=$1; shift ;;
    esac
done
[[ -n "$target_ip" ]] || { usage >&2; exit 1; }
# Allow IPv4/IPv6 literals, but no SSH options, user names or shell syntax.
[[ "$target_ip" =~ ^[0-9A-Fa-f:.]+$ ]] || fail 'TARGET_IP must be an IP address'
for command in ssh scp gzip sha256sum stat mktemp; do
    command -v "$command" >/dev/null || fail "Missing host command: $command"
done
root_image="$images/Standard-Image-moducop-cpu01.ext4"
data_image="$images/Standard-Image-moducop-cpu01.dataimg"
for image in "$root_image" "$data_image"; do
    [[ -s "$image" ]] || fail "Missing or empty image: $image"
done
(( $(stat -Lc %s "$root_image") <= 4294967296 )) || fail 'Rootfs image exceeds the 4 GiB root partition'
work_dir=$(mktemp -d /tmp/cpu01-recovery.XXXXXX)
ssh_options=(-o "ControlPath=$work_dir/ssh" -o ControlMaster=auto -o ControlPersist=60 -o ConnectTimeout=15)
remote="root@$target_ip"
scp_remote=$remote
[[ "$target_ip" != *:* ]] || scp_remote="root@[$target_ip]"
cleanup() {
    ssh "${ssh_options[@]}" -O exit "$remote" >/dev/null 2>&1 || true
    rm -rf -- "$work_dir"
}
trap cleanup EXIT

# This same read-only preflight is repeated immediately before erasing the card.
cat > "$work_dir/preflight.sh" <<'REMOTE'
set -eu
fail() { echo "Target check failed: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail 'Root login required'
for command in lsblk findmnt blockdev parted partprobe gzip sha256sum e2fsck resize2fs mount umount sed grep ln sync df mktemp; do
    command -v "$command" >/dev/null || fail "Missing command: $command"
done
grep -aq 'fsl,imx8mm' /proc/device-tree/compatible || fail 'Target is not an i.MX8M Mini CPU01'
[ -b /dev/mmcblk1 ] || fail 'SD device /dev/mmcblk1 is missing'
[ "$(cat /sys/class/block/mmcblk1/device/type)" = SD ] || fail '/dev/mmcblk1 is not an SD card'
case "$(findmnt -rn -o SOURCE /)" in
    /dev/mmcblk0p*) ;;
    *) fail 'Root filesystem must be on eMMC; refusing to overwrite an active recovery card' ;;
esac
case "$(findmnt -rn -o SOURCE /data)" in
    /dev/mmcblk0p*) ;;
    *) fail '/data must be on eMMC for staging the images' ;;
esac
# Refuse swap or other active uses that unmounting cannot release.
if grep -q '/dev/mmcblk1' /proc/swaps; then fail 'SD card is being used as swap'; fi
[ "$(blockdev --getsize64 /dev/mmcblk1)" -ge 8000000000 ] || fail 'Use an SD card of at least 8 GB'
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS /dev/mmcblk1
echo 'Target preflight passed (eMMC Linux, SD card at /dev/mmcblk1).'
REMOTE
printf 'Checking CPU01 at %s...\n' "$target_ip"
ssh "${ssh_options[@]}" "$remote" 'sh -s' < "$work_dir/preflight.sh"
card_bytes=$(ssh "${ssh_options[@]}" "$remote" 'blockdev --getsize64 /dev/mmcblk1')
[[ "$card_bytes" =~ ^[0-9]+$ ]] || fail 'Could not determine SD capacity'
(( $(stat -Lc %s "$data_image") <= card_bytes - 4097 * 1024 * 1024 - 1048576 )) || \
    fail 'Data image exceeds the available SD data partition'
if ((check_only)); then
    printf 'Checks passed. No images transferred and no SD changes made.\n'
    exit 0
fi
printf '\nThis will erase ALL contents of %s:/dev/mmcblk1.\n' "$target_ip"
if ((!assume_yes)); then
    read -r -p 'Type ERASE to continue: ' answer
    [[ "$answer" = ERASE ]] || fail 'Cancelled'
fi
printf 'Compressing filesystem images...\n'
gzip -1 -c "$root_image" > "$work_dir/rootfs.ext4.gz"
gzip -1 -c "$data_image" > "$work_dir/data.ext4.gz"
(
    cd "$work_dir"
    sha256sum rootfs.ext4.gz data.ext4.gz > SHA256SUMS
)
# Fixed safe path names; no caller-supplied strings are interpolated into remote code.
stage=$(ssh "${ssh_options[@]}" "$remote" 'mktemp -d /data/cpu01-sd-recovery.XXXXXX')
[[ "$stage" =~ ^/data/cpu01-sd-recovery\.[A-Za-z0-9]+$ ]] || fail 'Unexpected remote staging path'
required_kb=$(( ($(stat -c %s "$work_dir/rootfs.ext4.gz") + $(stat -c %s "$work_dir/data.ext4.gz")) / 1024 + 16384 ))
available_kb=$(ssh "${ssh_options[@]}" "$remote" "df -Pk '$stage' | awk 'NR == 2 {print \$4}'")
[[ "$available_kb" =~ ^[0-9]+$ ]] || fail 'Could not determine target staging space'
((available_kb >= required_kb)) || fail "Insufficient free space on target /data; staging directory: $stage"

cat "$work_dir/preflight.sh" > "$work_dir/install.sh"
cat >> "$work_dir/install.sh" <<'REMOTE'
# Executed with the staging directory as the current directory.
sha256sum -c SHA256SUMS
gzip -t rootfs.ext4.gz data.ext4.gz
mount_dir=$(mktemp -d /run/cpu01-sd-recovery.XXXXXX)
root_mounted=0
data_mounted=0
cleanup_mounts() {
    if [ "$data_mounted" = 1 ]; then umount "$mount_dir/data" || true; fi
    if [ "$root_mounted" = 1 ]; then umount "$mount_dir/root" || true; fi
    rmdir "$mount_dir/data" "$mount_dir/root" "$mount_dir" 2>/dev/null || true
}
trap cleanup_mounts EXIT
trap 'exit 1' HUP INT TERM
# Unmount all direct SD partition mounts, including the production automount.
for partition in /dev/mmcblk1p*; do
    [ -b "$partition" ] || continue
    findmnt -rn -o TARGET -S "$partition" > "$mount_dir/mounts" || :
    while IFS= read -r mountpoint; do
        umount "$mountpoint"
    done < "$mount_dir/mounts"
done
rm -f "$mount_dir/mounts"
# A busy device makes parted fail; never force past such a failure.
parted -s /dev/mmcblk1 mklabel msdos
parted -s /dev/mmcblk1 mkpart primary ext4 1MiB 4097MiB
parted -s /dev/mmcblk1 mkpart primary ext4 4097MiB 100%
partprobe /dev/mmcblk1
if command -v udevadm >/dev/null; then udevadm settle; fi
[ -b /dev/mmcblk1p1 ] && [ -b /dev/mmcblk1p2 ]
echo 'Writing rootfs and data filesystems to SD...'
gzip -dc rootfs.ext4.gz > /dev/mmcblk1p1
gzip -dc data.ext4.gz > /dev/mmcblk1p2
sync
check_fs() {
    status=0
    # SSH has no terminal: repair only problems e2fsck considers safe automatically.
    e2fsck -f -p "$1" || status=$?
    [ "$status" -le 1 ] || fail "e2fsck failed for $1 (status $status)"
    resize2fs "$1"
}
check_fs /dev/mmcblk1p1
check_fs /dev/mmcblk1p2
mkdir "$mount_dir/root" "$mount_dir/data"
mount /dev/mmcblk1p1 "$mount_dir/root"
root_mounted=1
mount /dev/mmcblk1p2 "$mount_dir/data"
data_mounted=1
root="$mount_dir/root"
data="$mount_dir/data"
grep -q /dev/mmcblk0p4 "$root/usr/sbin/init" || fail 'Unexpected init wrapper in rootfs image'
sed -i 's|/dev/mmcblk0p4|/dev/mmcblk1p2|g' "$root/usr/sbin/init"
sed -i '\|/media/sdcard|d; \|[[:space:]]/uboot[[:space:]]|d; \|[[:space:]]/data[[:space:]]|d' "$root/etc/fstab"
printf '\n/dev/mmcblk1p2 /data ext4 defaults 0 2\n' >> "$root/etc/fstab"
for unit in mender-client mender-authd mender-updated mender-connect mender-grow-data mender-systemd-growfs-data; do
    ln -sf /dev/null "$root/etc/systemd/system/$unit.service"
done
rm -f "$data/mender/bootstrap.mender"
[ -s "$root/boot/Image.gz" ] || fail 'Missing SD kernel'
[ -s "$root/boot/freescale/imx8mm-verdin-wifi-moducop-cpu01.dtb" ] || fail 'Missing CPU01 DTB'
grep /dev/mmcblk1p2 "$root/usr/sbin/init" "$root/etc/fstab"
sync
umount "$data"
data_mounted=0
umount "$root"
root_mounted=0
cleanup_mounts
trap - EXIT
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS /dev/mmcblk1
echo 'Recovery SD card prepared successfully.'
REMOTE
printf 'Transferring images to %s:%s...\n' "$target_ip" "$stage"
scp -O "${ssh_options[@]}" "$work_dir/rootfs.ext4.gz" "$work_dir/data.ext4.gz" \
    "$work_dir/SHA256SUMS" "$work_dir/install.sh" "$scp_remote:$stage/"
printf 'Installing and adapting the recovery SD card...\n'
if ! ssh "${ssh_options[@]}" "$remote" "cd '$stage' && sh install.sh"; then
    fail "Installation failed. Staged files retained at $target_ip:$stage for diagnosis."
fi
ssh "${ssh_options[@]}" "$remote" "rm -rf '$stage'"
printf '\nDone. Target remains on eMMC; reboot and use the SD U-Boot commands in:\n%s\n' \
    "$repo_dir/doc/cpu01-sdcard-recovery.md"
