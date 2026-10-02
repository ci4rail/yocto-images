# Legacy-to-current migration images

This builds an SD recovery bundle for CPU01 (i.MX8MM, 2 GiB) and
CPU01plus (i.MX8MP, 4 or 8 GiB). The installer runs from a RAM-resident
SquashFS in `recovery.itb`. The OS payload is stored separately in `image/`
on SD partition 1 and mounted read-only; **leave the SD card inserted until
installation and restoration finish**. This keeps the recovery FIT below
U-Boot's 256 MiB verification/load limit.

Startup obtains an address on `eth0` using DHCP and exposes the TEZI UI over
VNC on port 5900. Connect a VNC client to that address and select the migration
payload. `autoinstall=false`; booting never starts installation. The local
HTTP feed uses TEZI's USB gadget address internally, without an external server.
Custom preparation/restoration hooks preserve Mender state.

The default build requires FIT signing credentials and a prebuilt HAB-signed
imx-boot from the secure Yocto build. There is no separate rootfs signature or
dm-verity requirement for the installer. The FIT configuration signature covers
the kernel, DTB and ramdisk hashes. The signed runtime also pins the checksum
manifest for the SD payload. Production signing runs only in CI; local staging
keys are usable only on devices provisioned with matching staging trust anchors.

**Status: implementation and host tests, not a hardware-qualified release.**
Validate the checklist below on each platform before distributing bundles.
No production OS payload is included in this repository.

## Preservation and fuse policy

- Preserve only the complete `/data/mender` directory, including ownership,
  permissions, ACLs, extended attributes, symlinks and hard links.
- Find a unique ext3/ext4 eMMC partition with a real `mender` directory at its
  root. This supports a separate legacy `/data` partition; embedded `/data`
  inside a legacy root filesystem is deliberately unsupported.
- Inspect the old filesystem read-only without journal replay. If its check
  finds errors, run `e2fsck -fy` on the uniquely identified, unmounted legacy
  data partition, then require a clean read-only check. Verify the tar backup
  before any flashing. A failed repair or a result requiring reboot stops
  installation; its output is saved in `/run/migration/legacy-fsck-repair.log`.
- Abort on missing/ambiguous Mender data, mounted eMMC, active swap,
  insufficient memory/storage, an oversized backup, or corrupt payload files.
- Restore into partition 4 after TEZI installation, then unmount, reopen
  read-only, and compare restored contents against the backup.
- Retain backup and logs until the operator reboots. Never reboot automatically.
- Never program secure-boot fuses. Original package hooks are replaced, not
  executed. Fuse-related U-Boot environment entries are rejected at build time.
  Use trusted CI payloads whose bootloader/first-boot logic also leaves fuses alone.

**This is a destructive repartition, not an atomic OTA update.** Everything on
the target layout except the restored `/data/mender` contents is replaced.
The backup at `/run/migration/backup/mender.tar` is in RAM: it does not survive
power loss. Before migration, export `/data/mender` off the device, stop any
active Mender deployment, shut down the legacy OS cleanly, and maintain power
until restoration completes. A failed installation must not be retried blindly.
Automatic filesystem repair answers yes to all fixes and can change or discard
legacy data before the RAM backup is created. The off-device export is essential
when repair is possible.

Installing the signed production OS leaves an open device open. Hardware
secure-boot enforcement is unchanged because fuses are untouched.

## Build inputs

Install host tools on Debian/Ubuntu:

```sh
sudo apt-get install python3 u-boot-tools device-tree-compiler squashfs-tools e2fsprogs gcc-aarch64-linux-gnu libc6-dev-arm64-cross
```

Supply these trusted inputs separately for each platform:

1. An extracted **TEZI runtime** for Verdin iMX8MM or Verdin iMX8MP. The packaging
   smoke test used TEZI `7.7.0+build.13`; pin the runtime and its SHA-256 in your
   release process. The runtime must include GNU tar, `tezictl`, Qt's
   Wayland platform plugin and Weston VNC backend. Get it from Toradex:
   [Verdin iMX8M Mini — CPU01](https://tezi.toradex.com/artifactory/tezi-oe-prod-frankfurt/scarthgap-7.x.y/release/13/verdin-imx8mm/tezi/tezi-run/oedeploy/Verdin-iMX8MM_ToradexEasyInstaller_7.7.0%2Bbuild.13.zip)
   [Verdin iMX8M Plus — CPU01plus](https://tezi.toradex.com/artifactory/tezi-oe-prod-frankfurt/scarthgap-7.x.y/release/13/verdin-imx8mp/tezi/tezi-run/oedeploy/Verdin-iMX8MP_ToradexEasyInstaller_7.7.0%2Bbuild.13.zip)
2. A **ModuCop DTB compatible with that installer's kernel**. This is not the
   signed OS FIT. Build the matching board DTB from the corresponding BSP;
   do not assume that arbitrary kernel/DTB versions are interchangeable.
3. The current platform's `.mender_tezi.tar` produced by the standard-image
   build. Use the production artifact signed in CI for field installation;
   staging artifacts are only for development.
4. The target OS build's `/etc/fw_env.config` (the actual contents, resolving
   any symlink). The current supported format has two 8 KiB environment copies
   in eMMC boot0 at offsets `-0x2200` and `-0x4200`. The installer rewrites the
   device name to `/dev/emmc-boot0`. Stock TEZI's single-copy configuration is
   not used.

5. A HAB-signed CPU01 **imx-boot** built with the fixed SD recovery patch in
   `meta-ci4rail-bsp/recipes-bsp/u-boot`. Its SRK must match the closed device's
   fuses, and its trusted FIT public key must match the recovery signing key.
   The packager copies this artifact unchanged; it does not sign imx-boot or
   validate its HAB chain. Do not use stock `imx-boot-recoverytezi` on a closed
   device. The installed payload's bootloader must also match the fused trust.
6. The existing RSA2048 FIT key/certificate pair (`<name>.key`, `<name>.crt`),
   or the CI HSM token URI with `--fit-engine pkcs11` and `--fit-keyname` set to
   its key label. No new keys are generated by the packager.

CPU01 staging example, run from the repository root (requires the separately
built staging SD recovery imx-boot):

```sh
python3 migration/build.py \
  --platform cpu01 \
  --tezi migration/tezi/Verdin-iMX8MM_ToradexEasyInstaller_7.7.0+build.13/ \
  --dtb cpu01-standard-image/install/images/moducop-cpu01/imx8mm-verdin-wifi-moducop-cpu01.dtb \
  --fw-env-config cpu01-standard-image/install/images/moducop-cpu01/fw_env.config.default \
  --payload cpu01-standard-image/install/images/moducop-cpu01/Standard-Image-Staging-moducop-cpu01.mender_tezi.tar \
  --fit-keydir cpu01-standard-image/secure-boot-material/staging/keys/fit \
  --fit-keyname dev \
  --signed-imx-boot gen/uboot-sd-recovery/flash-cpu01-sd-recovery-staging.bin \
  --output gen/cpu01-migration
```

For CPU01plus, use `--platform cpu01plus`, the Verdin iMX8MP runtime,
`imx8mp-verdin-wifi-moducop-cpu01.dtb`, its target environment configuration,
its signed standard-image payload, and `--output gen/cpu01plus-migration`.
This packager consumes an already-built OS; it does not add production signing
support to the CPU01plus standard-image build.

The output contains `recovery.itb`, `imx-boot-signed`, the `image/` payload,
`sd.cmd`, legacy USB/TFTP scripts and sources, `build.json` recording input
hashes, and recursive `SHA256SUMS`. The output directory must not exist.
Original inputs are not modified. `--allow-unsigned` explicitly permits an
unsigned development bundle for open devices only.

## Boot from SD (mmc1)

Prepare SD partition 1 as FAT32, starting at 8 MiB or later. Copy
`recovery.itb` and the complete `image/` directory to its root. U-Boot uses
`mmc 1:1`; Linux finds the SD card by its sysfs card type, not by assuming
Linux uses the same MMC numbering. Discovery waits up to 30 seconds for the
card/partition device. For FIT boot through the existing U-Boot, an unpartitioned
card containing a whole-card FAT filesystem is also accepted; do not write raw
imx-boot into such a filesystem. Use the partitioned layout below for ROM SD boot.

If payload discovery or UI startup fails, the installer returns to its serial
shell instead of exiting PID 1. DHCP starts before payload discovery. Check:

```sh
cat /run/migration/media.log
cat /run/migration/dhcp.log
ip addr show eth0
ls -l /dev/mmcblk*
```

A missing `mmcblk1p1` with an existing `mmcblk1` can indicate a whole-card
filesystem or an unreadable/unsupported partition table. The runtime accepts
whole-card media only when `blkid` identifies FAT and no partition is present.

If eMMC already contains the signed U-Boot with the fixed recovery patch,
inserting this card makes it load `/recovery.itb#recovery` before environment
boot policy. No unsigned boot script or interactive CLI is needed.

To also boot the initial imx-boot from SD on CPU01, write `imx-boot-signed`
into the unpartitioned area at **33 KiB**, preserving the partition table.
For example, with `/dev/sdX` replaced by the verified SD device:

```sh
sudo dd if=gen/cpu01-migration/imx-boot-signed of=/dev/sdX bs=1024 seek=33 conv=notrunc,fsync
```

This offset is CPU01-specific (CPU01plus uses 32 KiB). Ensure the bootloader
fits before partition 1. The board's boot source must select SD for ROM boot;
inserting a card alone does not change the fused/strap boot source. A closed
device requires a matching HAB-signed bootloader whether loaded from SD or eMMC.

The DTB is given the exact `required-bootargs` expected by the recovery patch;
the FIT configuration is named `recovery`. Rootfs A/B signing and dm-verity
inside the installed OS payload remain properties of that supplied payload.

## Supported payload and memory limits

The validator accepts this project's current TEZI format 4 layout only:

1. Shared ext4 BOOTFIT partition.
2. Raw rootfs A.
3. Identical initial raw rootfs B.
4. Raw ext4 data image, with a maximized partition.
5. Bootloader components in eMMC boot0 and the supplied U-Boot environment.

TEZI retains responsibility for partition creation, bootloader writes and
Toradex configuration-block handling. The stock no-op preparation and data
partition resize hooks are replaced. TEZI already maximizes partition 4;
the data filesystem retains the supplied image's size. Before erasing, the
hook checks that this filesystem has enough free space for the backup plus
16 MiB. Other layouts and custom installation semantics need explicit support.

| Platform | Minimum installed RAM | Maximum complete FIT | Maximum RAM backup |
| --- | --- | --- | --- |
| CPU01 | 2 GiB | 256 MiB | 128 MiB |
| CPU01plus | 4 GiB | 256 MiB | 128 MiB |

The FIT limit matches the fixed recovery loader. Payload files stay on SD,
while the installer and Mender backup reside in RAM. The runtime checks
`MemAvailable` before creating the backup.

## Boot from USB (NOT TESTED)

Copy the generated bundle contents into the root directory of a FAT32 USB stick.
Remove unrelated boot scripts and installer images from that stick.
The SD card containing `image/` must also be inserted. Installation starts
only after selecting the payload in TEZI; preflight checks then run.

At the legacy U-Boot prompt:

```text
usb start
setenv devtype usb
setenv devnum 0
setenv distro_bootpart 1
setenv prefix /
load usb 0:1 ${scriptaddr} /boot.scr
source ${scriptaddr}
```

Adjust the USB device/partition if needed. These commands do not call `saveenv`.
The script loads the entire FIT at `0x60000000` and clears inherited FDT/ramdisk
relocation limits before booting its single `recovery` configuration.

## Boot over TFTP

Example for CPU01. TFTP server 192.168.24.70:

Place  `recovery.itb` in the TFTP server as `moducop/migration.imx8mm.itb`. Configure network
addresses in the existing U-Boot, for example:

```
setenv autoload no; dhcp; setenv serverip 192.168.24.70; setenv netmask 255.255.255.0; setenv bootargs console=ttymxc0,115200 root=/dev/ram0 rootfstype=squashfs ro ramdisk_size=393216; setenv fdt_high; setenv initrd_high; setenv bootm_low 0x40000000; setenv bootm_size 0x58000000; tftpboot 0x60000000 moducop/migration.imx8mm.itb; bootm 0x60000000#recovery
```

The SD payload card is still required when loading the FIT over TFTP or USB.
Startup brings up `lo` for `tezictl`, requests DHCP on `eth0`, assigns
`192.168.11.1` to TEZI's `usb0` gadget, and serves the verified payload there.
Use VNC at the DHCP address, port 5900, to select and install the payload.
Serial output uses `ttymxc0,115200` on CPU01 and `ttymxc2,115200` on CPU01plus.
Wait for the explicit restoration-complete message, remove the SD card,
then reboot from eMMC. No automatic reboot occurs.

### Boot from eMMC (NOT TESTED)

CPU01 example:
```
mmc dev <device>
ext4ls mmc <device>:<partition> /
ext4load mmc <device>:<partition> 0x60000000 /recovery.itb
setenv bootargs console=ttymxc0,115200 root=/dev/ram0 rootfstype=squashfs ro ramdisk_size=393216
setenv fdt_high
setenv initrd_high
setenv bootm_low 0x40000000
setenv bootm_size 0x58000000
bootm 0x60000000#recovery
```
## Failure handling and qualification

If a failure is reported after flashing begins, **do not reboot**. Keep power
applied and copy `/run/migration/backup/mender.tar`, its `SHA256SUMS`, and the
logs under `/run/migration` to external storage. Protect that backup as it
contains device credentials. The installer shell remains available on serial.
Recovery after power loss uses the separately exported backup and USB/TFTP boot.

Host checks:

```sh
python3 -m unittest discover -s migration/tests -v
for script in migration/runtime/*.sh migration/runtime/rc.local; do sh -n "$script"; done
```

Before field release, qualify both platforms using real legacy devices:

- Closed-device SD boot with matching production keys, rejection of an altered
  FIT, correct serial console, eMMC alias, product identification, kernel/DTB
  compatibility, DHCP (including late cable insertion), VNC UI and manual
  installation. Also check missing/corrupt SD payload and legacy USB/TFTP boots.
- Actual production FIT size, boot-time relocation and peak memory on 2 GiB
  CPU01 and 4 GiB CPU01plus; repeat on 8 GiB CPU01plus.
- Wrong platform, missing/corrupt/ambiguous Mender data, insufficient space,
  payload corruption and interrupted installation.
- Partition geometry, boot0 and redundant U-Boot environment, preserved Toradex
  config block, both signed FITs and rootfs slots, and first boot with dm-verity.
- Mender credentials, file metadata, database compatibility across client
  versions, artifact reporting, server authentication, and a subsequent A/B OTA.
- Fuse state unchanged before and after installation and first boot.

References: [TEZI loading](https://developer.toradex.com/easy-installer/toradex-easy-installer/loading-toradex-easy-installer/),
[TEZI customization](https://developer.toradex.com/easy-installer/toradex-easy-installer/toradex-easy-installer-create-image/),
[TEZI image format and hooks](https://developer.toradex.com/easy-installer/toradex-easy-installer/toradex-easy-installer-configuration-files/).
