#!/bin/bash
#
# Creates a bootable USB image from a given rootfs ext4 image.
#
set -e
set -x
apt-get update && apt-get install -y parted u-boot-tools udev kpartx

if [[ $(id -u) != 0 ]]; then
  echo "Error: Need elevated privileges. Run again with 'sudo'!"
  exit 1
fi

if [ -z ${2} ]; then
  echo "Usage: $0 <input-rootfs-image> <output-disk-image>"
  exit 1
fi

FINAL_SIZE=$((1*1024*1024*1024))

# extract the input ext4 image
input_image=$(realpath "$1")
output_image=$(realpath "$2")

tmp_dir=$(mktemp -d -t usbimage-XXXXXXXXXX)
mkdir -p "$tmp_dir"
trap 'rm -rf "$tmp_dir"' EXIT

mount -o loop "$input_image" "$tmp_dir"
trap 'umount "$tmp_dir"' EXIT

SECTOR_SIZE=512
IMAGE_SECTORS=$(($FINAL_SIZE/SECTOR_SIZE))

# create empty file
dd if=/dev/zero bs=${SECTOR_SIZE} count=${IMAGE_SECTORS} > ${output_image}

# create partition table and one partition
parted -a optimal --script ${output_image} mklabel gpt mkpart primary 1MB $((${IMAGE_SECTORS}*${SECTOR_SIZE}-1024*1204))B

# Scan for partition
LOOP_DEV=`losetup --partscan --show --find ${output_image}`

# Map partitions (creates /dev/mapper/loopXp1)
kpartx -a ${LOOP_DEV}
sleep 1
PART_DEV="/dev/mapper/$(basename ${LOOP_DEV})p1"
trap 'kpartx -d ${LOOP_DEV}' EXIT
trap 'losetup -d ${LOOP_DEV}' EXIT

echo "Loop device: ${LOOP_DEV}"
echo "Partition device: ${PART_DEV}"

# create file system on partition
mkfs.ext4 ${PART_DEV}

# create mount point
mkdir -p /mnt/usb

# mount the partition
mount ${PART_DEV} /mnt/usb
trap 'umount /mnt/usb' EXIT

# copy the ext4 image to the partition
tar -C "$tmp_dir" -cf - . | tar -C /mnt/usb -xf -
