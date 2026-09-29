#!/bin/bash
#
# Create filesystem image and copy the contents of source-dir into that image
#
set -e
if [[ $(id -u) != 0 ]]; then
  echo "Error: Need elevated privileges. Run again with 'sudo'!"
  exit 1
fi

if [ -z ${2} ]; then
  echo "Usage: make-gadget-image <input> <image-file>"
  exit 1
fi

if ! [ -x "$(which jq)" ]; then
  echo 'Error: jq is not installed.' >&2
  exit 1
fi

if ! [ -x "$(which parted)" ]; then
  echo 'Error: parted is not installed.' >&2
  exit 1
fi

temp_dir_set=false
if [ -n "$(file -Lb "${1}" | grep 'tar archive')" ]; then
  echo "Input is .tar. Extracting first."
  tmp_dir=$(mktemp -d -t make-gadget-image-XXXXXXXXXX)
  tar xfv ${1} -C ${tmp_dir}
  SOURCE_DIR=${tmp_dir}/*
  temp_dir_set=true
elif [ -n "$(file -b ${1} | grep 'directory')" ]; then
  SOURCE_DIR=${1}
else
  echo "Error: invalid input. ${1} is not a .tar file or directory." >&2
  exit 1
fi

IMAGE_FILE=${2}

# Get size of extracted tar
FILE_SIZE=$(du -b ${SOURCE_DIR} | awk '{print $1}')
# Add extra space by percentage
EXTRA_PERCENTAGE=50
EXTRA_SIZE=$(($FILE_SIZE*$EXTRA_PERCENTAGE/100))
FINAL_SIZE=`expr $FILE_SIZE + $EXTRA_SIZE`
MODULO_SIZE=$(($FINAL_SIZE%2048))
FINAL_SIZE=`expr $FINAL_SIZE - $MODULO_SIZE`

# image size in 512 byte sectors
SECTOR_SIZE=512
IMAGE_SECTORS=$(($FINAL_SIZE/SECTOR_SIZE))

# create empty file
dd if=/dev/zero bs=${SECTOR_SIZE} count=${IMAGE_SECTORS} > ${IMAGE_FILE}

# create partition table and one partition
parted -a optimal --script ${IMAGE_FILE} mklabel gpt mkpart primary 1MB $((${IMAGE_SECTORS}*${SECTOR_SIZE}-1024*1204))B

# Scan for partition
LOOP_DEV=`losetup --partscan --show --find ${IMAGE_FILE}`
PART_DEV=${LOOP_DEV}p1

# create file system on partition
mkfs.ext4 ${PART_DEV}

# mount created file system
MOUNT_DIR=`mktemp -d`
mount ${PART_DEV} ${MOUNT_DIR}

# Copy source data to file system
cp -r ${SOURCE_DIR}/* ${MOUNT_DIR}

# Set autoinstall to true
jq '.autoinstall = true' ${MOUNT_DIR}/image.json > ${MOUNT_DIR}/image_mod.json
mv ${MOUNT_DIR}/image_mod.json ${MOUNT_DIR}/image.json

# Adapt wrapup.sh that tdx-installer knows when flashing is complete
# but only if it is not already modified
if ! grep -Fq 'echo "tezi-installer-finished"' "${MOUNT_DIR}/wrapup.sh"; then
  cat ${MOUNT_DIR}/wrapup.sh
  echo -e '#/bin/bash\n\necho "tezi-installer-finished" > /dev/console\nexit 0' > "${MOUNT_DIR}/wrapup.sh"
  echo "wrapup.sh modified"
else
  echo "wrapup.sh not touched"
fi

# cleanup
umount ${PART_DEV}
rmdir ${MOUNT_DIR}
losetup -d ${LOOP_DEV}
if [ "${temp_dir_set}" == true ]; then
  rm -rf ${tmp_dir}
fi
