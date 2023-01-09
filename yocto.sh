#!/bin/bash

source config/custom.env
source config/secret.env

TARGET=$(whiptail --title "Yocto Make GUI" --menu "Choose target" 15 110 5 \
"image" " - build a yocto image" \
"yocto-shell" " - start kas with a yocto shell" \
"mender-upload" " - upload latest image to mender server" \
"mender-deploy" " - deploy latest image to device with device id ${MENDER_DEVICE_ID}" \
"mender" " - combine \"image\", \"mender-upload\", \"mender-deploy\"" \
3>&1 1>&2 2>&3)

if [[ $? != 0 ]]; then
    echo "Yocto Make GUI Cancelled!"
    exit
fi

if [[ ${TARGET} == "mender"* ]]; then
    mender-cli login --username ${MENDER_USERNAME} --password ${MENDER_PASSWORD}
fi

IMAGE=$(whiptail --title "Yocto Make GUI" --menu "Choose image" 15 100 8 \
"cpu01-devtools-image" " - CPU01 Devtools" \
"cpu01-edgfarm-image" " - CPU01 Edgefarm" \
"cpu01-edgefarm-devtools-image" " - CPU01 Edgefarm Devtools" \
"cpu01plus-devtools-image" " - CPU01 (iMX8MPlus) Devtools" \
"cpu01plus-edgfarm-image" " - CPU01 (iMX8MPlus) Edgefarm" \
"cpu01plus-edgefarm-devtools-image" " - CPU01 (iMX8MPlus) Edgefarm Devtools" \
"raspberrypi4-64-edgfarm-image" " - RaspberryPi 4 (64Bit) Edgefarm" \
"raspberrypi4-64-edgefarm-devtools-image" " - RaspberryPi 4 (64Bit) Edgefarm Devtools" \
3>&1 1>&2 2>&3)

if [[ $? != 0 ]]; then
    echo "Yocto Make GUI Cancelled!"
    exit
fi

echo "Target: ${TARGET}"
echo "Image: ${IMAGE}"


make IMAGE_DIR=${IMAGE} ${TARGET}
