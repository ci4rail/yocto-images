[.] implemented, untested
[x] implemented, tested
[!] problem
[-] don't need

# TODOs
[x] ETH0 LEDs
[x] Kernel configs for modem
[x] Kernel configs for docker
[x] Wifi support
[x] io4edge support
[.] socketcan-io4edge
[x] ttynvt
[x] tailscale
[x] chrony
[x] gpsd
[x] don't allow modem manager to access GPS device
[x] docker (podman)
[x] docker compose (podman-compose)
[x] systemd waitnetworkonline

[x] MEC Inventory in EEPROM
[x] sysfs entry for CPU01 EEPROM

[x] mender-connect.conf
[ ] add standard image


## Features not immediately required
[x] mender-docker compose
[x] ETH2 support
[.] LAN7431 fixed PHY  -- ist schon im kernel
[ ] LAN743x backport ??? -> no
[.] Moducop ign shutdown
[.] scheduled reboot
[x] sdcard automount
[.] IOU06 sound support
[-] kea (dhcp server) config ?
[ ] wifi AP

[ ] remove verdin CAN
[ ] bring back stressapptest

## build system

[ ] fix git URLs
[x] fix kas:  Using deprecated refspec for repository "src/bitbake".
[x] update ci to dunfell branch


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
    Mainly compatible with docker. Major difference is that podman requires always registry in container url

rootfs image size changed to 2500M -> upgrade from previous image impossible

CPU01 EEPROM now available via sysfs /sys/bus/i2c/devices/3-0050/eeprom

New hostname (MEC01-<moducop-system-serialnumber>)

ee-inv tool to read out the inventory from the EEPROM

# ISSUES:

Cpu01plus:
 ETH2 not always recognized
ieee80211 phy0: mwifiex_cfg80211_sched_scan_start : Invalid Sched_scan parameters  -->  just a warning
