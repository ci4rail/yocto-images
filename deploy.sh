#!/bin/bash
TARGET=192.168.24.10
sudo scripts/make-gadget-image.sh cpu01-standard-image/install/images/moducop-cpu01/Standard-Image-Staging-moducop-cpu01.mender_tezi.tar tezi.img
scp tezi.img trooper@$TARGET:~/tezi.img
ssh -tt trooper@"$TARGET" 'cd ~/testfarm-helpers && robot-dev -t "Install Existing TEZI Image" helpers/'
rm -f tezi.img || true
