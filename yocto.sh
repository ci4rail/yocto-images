#!/bin/bash

source config/custom.env
source config/secret.env

TARGET=$(whiptail --title "Yocto Make GUI" --menu "Choose target" 15 110 6 \
"image" " - build a yocto image" \
"yocto-shell" " - start kas with a yocto shell" \
"mender-upload" " - upload latest image to mender server" \
"mender-deploy" " - deploy latest image to device with device id ${MENDER_DEVICE_ID}" \
"mender" " - combine \"image\", \"mender-upload\", \"mender-deploy\"" \
"upload_tezi" " - upload tezi to test computer (${TEZI_TARGET})" \
3>&1 1>&2 2>&3)

if [[ $? != 0 ]]; then
    echo "Yocto Make GUI Cancelled!"
    exit
fi

if [[ ${TARGET} == "mender"* ]]; then
    mender-cli login --username ${MENDER_USERNAME} --password ${MENDER_PASSWORD}
fi

IMAGE=$(whiptail --title "Yocto Make GUI" --menu "Choose image" 15 100 9 \
"cpu01-devtools-image" " - CPU01 Devtools" \
"cpu01-standard-image" " - CPU01 Standard" \
"cpu01-velog-image" " - CPU01 Velog" \
"cpu01plus-devtools-image" " - CPU01 (iMX8MPlus) Devtools" \
"cpu01plus-standard-image" " - CPU01 (iMX8MPlus) Standard" \
3>&1 1>&2 2>&3)

if [[ $? != 0 ]]; then
    echo "Yocto Make GUI Cancelled!"
    exit
fi

if [[ ${TARGET} == "upload_tezi" ]]; then
    if [[ ${IMAGE} == "cpu01-devtools-image" ]]; then TEZI="cpu01-devtools-image/install/images/moducop-cpu01/Devtools-Image-moducop-cpu01.mender_tezi.tar";
    elif [[ ${IMAGE} == "cpu01-standard-image" ]]; then TEZI="cpu01-standard-image/install/images/moducop-cpu01/Standard-Image-moducop-cpu01.mender_tezi.tar";
    elif [[ ${IMAGE} == "cpu01-velog-image" ]]; then TEZI="cpu01-velog-image/install/images/moducop-cpu01/Velog-Image-moducop-cpu01.mender_tezi.tar";
    elif [[ ${IMAGE} == "cpu01plus-devtools-image" ]]; then TEZI="cpu01plus-devtools-image/install/images/moducop-cpu01plus/Devtools-Image-moducop-cpu01plus.mender_tezi.tar";
    elif [[ ${IMAGE} == "cpu01plus-standard-image" ]]; then TEZI="cpu01plus-standard-image/install/images/moducop-cpu01plus/Standard-Image-moducop-cpu01plus.mender_tezi.tar";
    else echo "ERROR: Invalid image for upload selected!"; exit;
    fi

    echo "Upload ${TEZI} to ${TEZI_TARGET}..."
    scp ${TEZI} ${TEZI_TARGET}
    exit
fi

echo "Target: ${TARGET}"
echo "Image: ${IMAGE}"


echo "make IMAGE_DIR=${IMAGE} ${TARGET}"

make IMAGE_DIR=${IMAGE} ${TARGET}
