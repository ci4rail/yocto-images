#!/usr/bin/env bash
# Convert a TEZI archive or extracted directory into an auto-install USB disk.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
[[ $# == 2 ]] || { echo 'Usage: make-gadget-image.sh INPUT OUTPUT' >&2; exit 1; }
for tool in jq parted file tar losetup mkfs.ext4 mount umount du awk; do
    command -v "$tool" >/dev/null || { echo "Missing utility: $tool" >&2; exit 1; }
done
output=$(realpath -m "$2")
[[ ! -e "$output" ]] || { echo "Output already exists: $output" >&2; exit 1; }
work=$(mktemp -d)
loop=''
mounted=false
cleanup() {
    if $mounted; then umount "$work/mount"; fi
    if [[ -n "$loop" ]]; then losetup -d "$loop"; fi
    rm -rf "$work"
}
trap cleanup EXIT
mkdir "$work/source" "$work/mount"
if [[ -d "$1" ]]; then
    source=$(realpath "$1")
else
    # Python validates names and links before privileged extraction.
    python3 - "$1" "$work/source" <<'PY'
import pathlib, sys, tarfile
with tarfile.open(sys.argv[1]) as archive:
    for member in archive.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or member.issym() or member.islnk() or member.isdev():
            raise SystemExit(f'Unsafe archive member: {member.name}')
    archive.extractall(sys.argv[2])
PY
    mapfile -t manifests < <(find "$work/source" -name image.json -type f)
    [[ ${#manifests[@]} == 1 ]] || { echo 'Expected exactly one image.json' >&2; exit 1; }
    source=$(dirname "${manifests[0]}")
fi
jq -e . "$source/image.json" >/dev/null
bytes=$(du -sb "$source" | awk '{print $1}')
# Filesystem metadata and partition alignment need space even for small images.
size=$((bytes + bytes / 2 + 64 * 1024 * 1024))
truncate -s "$size" "$output"
parted --script "$output" mklabel gpt mkpart primary 1MiB 100%
loop=$(losetup --partscan --show --find "$output")
for ((i=0; i<50; i++)); do [[ -b "${loop}p1" ]] && break; sleep .1; done
mkfs.ext4 -F "${loop}p1"
mount "${loop}p1" "$work/mount"
mounted=true
cp -a "$source/." "$work/mount/"
jq '.autoinstall = true | .wrapup_script = "wrapup.sh"' "$work/mount/image.json" > "$work/image.json"
cp "$work/image.json" "$work/mount/image.json"
# Retain image-specific wrapup behavior and propagate failures.
if [[ -f "$work/mount/wrapup.sh" ]]; then
    mv "$work/mount/wrapup.sh" "$work/mount/wrapup-original.sh"
fi
cat > "$work/mount/wrapup.sh" <<'WRAP'
#!/bin/sh
set -e
if [ -f "$(dirname "$0")/wrapup-original.sh" ]; then
    sh "$(dirname "$0")/wrapup-original.sh" "$@"
fi
echo "tezi-installer-finished" > /dev/console
WRAP
chmod +x "$work/mount/wrapup.sh"
sync
# The unprivileged runner transfers the finished disk to the TC.
chmod 644 "$output"
