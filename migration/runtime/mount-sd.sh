#!/bin/sh
# Select SD by card type, never Linux MMC numbering.
mount_sd() {
    mkdir -p /run/media/migration-sd
    for attempt in $(seq 1 30); do
        for card in /sys/class/block/mmcblk*; do
            [ -f "$card/device/type" ] || continue
            [ "$(cat "$card/device/type")" = SD ] || continue
            disk="/dev/${card##*/}"
            partition="${disk}p1"
            if [ ! -b "$partition" ]; then
                [ -b "$disk" ] || continue
                [ ! -e "${card}p1" ] || continue
                # Do not mistake an unrecognized partition table for a filesystem.
                [ "$(blkid -s TYPE -o value "$disk")" = vfat ] || continue
                partition="$disk"
            fi
            echo "Mounting SD card on $partition"
            mount -o rw,nosuid,nodev "$partition" /run/media/migration-sd || continue
            echo "Mounted SD card at /run/media/migration-sd"
            return 0
        done
        sleep 1
    done
    echo 'No writable SD filesystem found within 30 seconds.'
    ls -l /dev/mmcblk* 2>/dev/null
    return 1
}
mount_sd
