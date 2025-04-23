[.] implemented, untested
[x] implemented, tested
[!] problem


# TODOs
[x] ETH0 LEDs
[ ] LAN7431 fixed PHY
[ ] LAN743x backport ???
[ ] Moducop ign shutdown
[x] Kernel configs for modem
[.] Kernel configs for docker
[ ] sdcard automount
[x] ETH2 support
IOU06 sound support
[x] Wifi support
[.] fsck on data partition
kea (dhcp server) config ?
[ ] io4edge support
[ ] socketcan-io4edge
[ ] ttynvt
[ ] tailscale
[.] chrony
[.] gpsd
[.] don't allow modem manager to access GPS device
[x] docker (podman)
[ ] docker compose
[ ] mender-docker compose
[.] systemd waitnetworkonline

[ ] MEC Inventory in EEPROM

[x] mender-connect.conf
[ ] wifi AP
[ ] add standard image

[ ] remove verdin CAN
[ ] bring back stressapptest

## build system


[ ] fix git URLs
[x] fix kas:  Using deprecated refspec for repository "src/bitbake".
[ ] update ci to dunfell branch


# NOTES

 PCIe clock strength
  Braucht es nicht:
            cpu01-devtools-image/local_repos/linux/drivers/phy/freescale/phy-fsl-imx8m-pcie.c
            writel(ANA_AUX_RX_TERM | ANA_AUX_TX_LVL,  == 0x9f
		        imx8_phy->base + IMX8MM_PCIE_PHY_CMN_REG065);
 Provide standard sensor monitoring via the HWMON
    Braucht es nicht:
        Already in the kernel


Neue device namen:
eth0 -> end0
wwan0 -> wwu1u1i5

New feature overlayfs-etc
 Replaces /usr/sbin/init with a script that overlays /etc, then start the original init
 machine-id now persistent

/home/root is now /root

docker no longer supported -> move to podman


# ISSUES:

rootfs overlay doesn't work
Cpu01plus:
 ETH2 not always recognized
ieee80211 phy0: mwifiex_cfg80211_sched_scan_start : Invalid Sched_scan parameters  -->  just a warning
