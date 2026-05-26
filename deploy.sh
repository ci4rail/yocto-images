#!/bin/bash
TARGET=192.168.24.10
scp cpu01-standard-image/install/images/moducop-cpu01/Standard-Image-moducop-cpu01.mender_tezi.tar trooper@$TARGET:~/tezi.tar
ssh -tt trooper@"$TARGET" 'cd ~/testfarm-helpers && robot-dev -t "Install TEZI tar" helpers/'
