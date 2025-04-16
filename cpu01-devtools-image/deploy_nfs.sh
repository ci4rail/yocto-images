#! /bin/bash
SERVER_IP="192.168.24.70"
BASE_DIR="cpu01-devtools-image"
SRV_DIR="mec_mm_yocto"

echo "installing rootfs"
ssh ${SERVER_IP} "cd /srv/fs && sudo rm -rf ${SRV_DIR} && sudo mkdir -p ${SRV_DIR} && sudo chmod 777 ${SRV_DIR} && cd ${SRV_DIR} && sudo tar xJf -" < ${BASE_DIR}/install/images/moducop-cpu01/Devtools-Image-moducop-cpu01.tar.xz
echo "installing bootfs"
ssh ${SERVER_IP} "cd /srv/fs/${SRV_DIR}/boot && sudo tar xJf -" < ${BASE_DIR}/install/images/moducop-cpu01/Devtools-Image-moducop-cpu01.bootfs.tar.xz

# Boot from TFTP/NFS
# setenv autoload no; dhcp; setenv serverip 192.168.24.70; tftp $fdt_addr_r mec_mm_yocto/boot/imx8mm-verdin-wifi-dev.dtb; tftp $kernel_addr_r mec_mm_yocto/boot/Image.gz; setenv bootargs root=/dev/nfs $console nfsroot=$serverip:/srv/fs/mec_mm_yocto,tcp,v3 rw ip=$ipaddr:$serverip:$gatewayip:$netmask:$hostname:$netdev:none ; booti $kernel_addr_r - $fdt_addr_r
