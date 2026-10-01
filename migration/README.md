# Legacy-to-current migration images

This builds two **RAM-resident migration bundles**, one for CPU01 (i.MX8MM,
2 GiB) and one for CPU01plus (i.MX8MP, 4 or 8 GiB). Both boot through the
existing U-Boot using USB mass storage or TFTP. The complete target TEZI
payload is embedded in the installer's compressed squashfs root filesystem.
Linux and the payload remain in RAM throughout installation.

The implementation repackages the Toradex Easy Installer runtime and uses its
flashing engine through a RAM-resident HTTP feed and `autoinstall=true`, with
an offscreen interface and custom preparation/restoration hooks. The local
server uses TEZI's USB gadget address; no external network service is needed
after boot. It does not need Docker, Mender, a display, or a network root
filesystem on the device. The installer runtime itself is
unsigned, intended for the existing open legacy devices.

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
   release process. The runtime must include GNU tar, `tezictl`, and Qt's
   offscreen platform plugin. Get it from Toradex:
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

CPU01 example, run from the repository root:

```sh
python3 migration/build.py \
  --platform cpu01 \
  --tezi migration/tezi/Verdin-iMX8MM_ToradexEasyInstaller_7.7.0+build.13/ \
  --dtb cpu01-standard-image/install/images/moducop-cpu01/imx8mm-verdin-wifi-moducop-cpu01.dtb \
  --fw-env-config cpu01-standard-image/install/images/moducop-cpu01/fw_env.config.default \
  --payload cpu01-standard-image/install/images/moducop-cpu01/Standard-Image-Staging-moducop-cpu01.mender_tezi.tar \
  --output gen/cpu01-migration
```

For CPU01plus, use `--platform cpu01plus`, the Verdin iMX8MP runtime,
`imx8mp-verdin-wifi-moducop-cpu01.dtb`, its target environment configuration,
its signed standard-image payload, and `--output gen/cpu01plus-migration`.
This packager consumes an already-built OS; it does not add production signing
support to the CPU01plus standard-image build.

The output contains `migration.itb`, `boot.scr`, `tftp.scr`, their readable
`.cmd` sources, `build.json` recording input hashes, and `SHA256SUMS`.
The repacked root filesystem uses gzip SquashFS, matching the stock TEZI
ramdisk and the installer's kernel support. Rebuild the bundle after changing
the packager; an older xz-compressed bundle fails to mount its root filesystem.
The output directory must not already exist. No original inputs are modified.
Checksums detect corruption; they do not authenticate the unsigned installer.
Distribute the bundle through the trusted release channel.

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
| CPU01 | 2 GiB | 384 MiB | 128 MiB |
| CPU01plus | 4 GiB | 768 MiB | 128 MiB |

These conservative FIT limits reserve space for the loaded FIT, boot-time
relocation, Linux's RAM disk, runtime memory, and backup. They require measurement
on hardware before increasing. The runtime also checks `MemAvailable` before
creating the backup. Oversized payloads fail the build; there is no silent
fallback to USB or HTTP payload streaming. Actual production payload fit has
not yet been established.

## Boot from USB

Copy the generated bundle contents into the root directory of a FAT32 USB stick.
Remove unrelated boot scripts and installer images from that stick.
Booting this bundle **starts migration automatically** after the preflight checks.

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
relocation limits before booting its single `migration` configuration.

## Boot over TFTP

Example for CPU01. TFTP server 192.168.24.70:

Place  `migration.itb` in the TFTP server as `moducop/migration.imx8mm.itb`. Configure network
addresses in the existing U-Boot, for example:

```
setenv autoload no; dhcp; setenv serverip 192.168.24.70; setenv netmask 255.255.255.0; setenv bootargs console=ttymxc0,115200 root=/dev/ram0 rootfstype=squashfs ro ramdisk_size=393216; setenv fdt_high; setenv initrd_high; setenv bootm_low 0x40000000; setenv bootm_size 0x58000000; tftpboot 0x60000000 moducop/migration.imx8mm.itb; bootm 0x60000000#migration
```

Once booted, no external network server or USB media is required for payload
access. Startup brings up `lo` for `tezictl`, assigns `192.168.11.1` to
TEZI's `usb0` gadget, and serves `/migration/image` on that address. The
embedded `image.json` has `autoinstall=true`; TEZI discovers it through the
local image feed. The server only serves validated image filenames.
Serial output uses `ttymxc0,115200` on CPU01 and `ttymxc2,115200` on CPU01plus.
Wait for the explicit restoration-complete message, remove USB installation
media, then reboot from eMMC. No automatic reboot prevents an installation loop
and leaves recovery information available.

## Failure handling and qualification

If a failure is reported after flashing begins, **do not reboot**. Keep power
applied and copy `/run/migration/backup/mender.tar`, its `SHA256SUMS`, and the
logs under `/run/migration` to external storage. Protect that backup as it
contains device credentials. The installer shell remains available on serial.
Recovery after power loss uses the separately exported backup and USB/TFTP boot.

Host checks:

```sh
python3 -m unittest discover -s migration/tests -v
for script in migration/runtime/*; do sh -n "$script"; done
```

Before field release, qualify both platforms using real legacy devices:

- USB and TFTP boots, correct serial console, eMMC alias, product identification,
  kernel/DTB compatibility, headless TEZI startup, local feed discovery and
  automatic installation.
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
