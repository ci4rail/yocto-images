[.] implemented, untested
[x] implemented, tested
[!] problem
[-] don't need

# TODOs
[x] ETH0 LEDs
[x] Kernel configs for modem
[x] Kernel configs for docker
[x] Wifi support
[!] fsck on data partition  -> fsck not in boot.cmd.in
[x] io4edge support
[.] socketcan-io4edge
[x] ttynvt
[x] tailscale
[.] chrony
[x] gpsd
[.] don't allow modem manager to access GPS device
[!] docker (podman)
[x] docker compose (podman-compose)
[x] systemd waitnetworkonline

[ ] MEC Inventory in EEPROM
[x] sysfs entry for CPU01 EEPROM

[x] mender-connect.conf
[ ] add standard image


## Features not immediately required
[ ] mender-docker compose
[x] ETH2 support
[ ] LAN7431 fixed PHY
[ ] LAN743x backport ???
[ ] Moducop ign shutdown
[x] sdcard automount
[ ] IOU06 sound support
[-] kea (dhcp server) config ?
[ ] wifi AP

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

rootfs image size changed to 2500MB

CPU01 EEPROM now available via sysfs /sys/bus/i2c/devices/3-0050/eeprom

# ISSUES:

Cpu01plus:
 ETH2 not always recognized
ieee80211 phy0: mwifiex_cfg80211_sched_scan_start : Invalid Sched_scan parameters  -->  just a warning
