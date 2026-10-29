# Manual migration images

The RAM-resident `recovery.itb` supports CPU01 (i.MX8MM) and CPU01plus
(i.MX8MP). Recovery and the TEZI OS payload are built independently. Copy
`recovery.itb` and the payload directory `image/` to SD partition 1.
Boot opens a password-protected root serial console, requests DHCP on eth0, mounts SD read/write, and
starts the TEZI UI. All migration operations are manual.

Devices whose legacy U-Boot cannot read the SD card can instead boot a
payload-embedding recovery from their old `/data` partition and install
unattended; see [Boot from the legacy /data partition](#boot-from-the-legacy-data-partition-no-sd-card).

## Build recovery

For CI builds, choose the platform’s **Recovery Staging** or **Recovery Production**
GitHub action, or call the reusable
[`recovery-workflow.yaml`](../.github/workflows/recovery-workflow.yaml).
See [CI configuration and input provenance](ci/README.md) for signing secrets,
platform support, and private MinIO storage.

Host dependencies (Debian/Ubuntu):

```sh
sudo apt-get install python3 u-boot-tools device-tree-compiler squashfs-tools gcc-aarch64-linux-gnu libc6-dev-arm64-cross openssl
umask 077
openssl passwd -6 > /path/to/recovery-root.hash
python3 migration/build-recovery.py \
  --platform cpu01 \
  --tezi migration/tezi/Verdin-iMX8MM_ToradexEasyInstaller_7.7.0+build.13 \
  --dtb /path/to/imx8mm-verdin-wifi-moducop-cpu01.dtb \
  --root-password-hash-file /path/to/recovery-root.hash \
  --fit-keydir /path/to/staging-keys --fit-keyname dev \
  --output gen/cpu01-recovery
```

`openssl passwd` prompts for the password without putting it in the command
line. Both recovery build entry points require `--root-password-hash-file`;
there is no default password. The SHA-512 crypt hash is embedded in the signed
recovery filesystem, not in `build.json`. Keep the hash file outside version
control. Changing the password requires rebuilding and signing recovery.
The serial console asks for the root password before opening a shell and again
after logout. DHCP and TEZI start before login. This protects only
the serial shell; TEZI's VNC interface remains unauthenticated.

Use the matching ModuCop installer-kernel DTB. Recovery includes
[`runtime/fw_env.config`](runtime/fw_env.config), matching this project's Mender
layout: two 8 KiB environment copies in boot0 at -0x2200 and -0x4200. TEZI uses
this configuration when installing the payload's `u_boot_env`; its stock
single-copy configuration is unsuitable. If the target layout changes, update
the bundled configuration to match. No exported configuration is required.
For CPU01plus use `--platform cpu01plus`, its iMX8MP
TEZI runtime and DTB. The runtime must include GNU tar, Weston with VNC,
TEZI and ext filesystem utilities. The bundled 7.7 runtime was used for host
packaging checks. A small static archive splitter is compiled for FAT32 backups.

Recovery output contains `recovery.itb`, boot scripts, input hashes in
`build.json`, and `SHA256SUMS`. No bootloader input is required: an existing
compatible U-Boot loads recovery, and `--fit-keydir` controls FIT signing.
The FIT is limited to
256 MiB. Production signing remains CI-only; staging requires matching device
trust anchors. `--allow-unsigned` is for open development devices only.

## Build payload

```sh
python3 migration/build-payload.py \
  --payload /path/to/standard-image.mender_tezi.tar \
  --output gen/cpu01-payload/image
```

This step needs only Python and the release archive. Supported product IDs are
preserved from the payload; TEZI checks device compatibility during installation.
It does not rebuild recovery. Copy the resulting `image/`
directory to the SD root. Outputs must not exist; inputs are left unchanged.
The original `build.py` CLI still builds both together with all the above options.

Payload validation retains the four-partition BOOTFIT/rootfs A/rootfs B/data
layout, boot0 and environment checks. `autoinstall` is always false. All installation
hooks are removed. No payload hook backs up or restores data. TEZI maximizes
the data partition, but its filesystem retains the supplied image size; the
bundled TEZI does not include `resize2fs`. If the backup will not fit, enlarge
the filesystem separately before restoring. No HTTP image list
is generated. Use trusted release payloads: recovery no longer pins a specific
payload checksum manifest. `SHA256SUMS` detects accidental corruption, not
replacement by another image. No secure-boot fuses are programmed by these tools.

## Manual backup, install and restore

Identify the old separate `/data` partition using `blkid` and the legacy device's
partition layout. The scripts require an explicit eMMC partition; they do not
guess that old `/data` is partition 4. Example only, **after checking the device**:

```sh
/migration/mount-sd.sh
mkdir -p /run/media/migration-sd/backups
/migration/backup-data.sh /run/media/migration-sd/backups/device-001 /dev/mmcblk2p4
umount /run/media/migration-sd
```

TEZI owns local-media mounting during discovery and installation. Run manual
SD backup/restore operations only while TEZI is idle, and unmount the SD card
afterwards so TEZI can mount it itself.

The new backup folder must not exist. The script checks that eMMC is unmounted,
checks the ext3/ext4 filesystem without repair, mounts it read-only without
journal replay, and archives **all contents** including dotfiles, ownership,
modes, ACLs, extended attributes, symlinks, hard links and sparse files. It writes
1 GiB archive chunks for FAT32 compatibility, compares the archive against the
source, writes checksums and a completion marker, and flushes to SD. There is no
RAM backup size limit. Have enough free SD space for the complete archive.
Unclean filesystems require operator attention; backup never repairs them.

Once backup succeeds, connect VNC to the eth0 DHCP address on port 5900 and
select the payload in TEZI. Installation repartitions eMMC and erases the old
data. After TEZI finishes, explicitly restore to the new data partition:

```sh
/migration/mount-sd.sh
/migration/restore-data.sh /run/media/migration-sd/backups/device-001 /dev/mmcblk2p4
umount /run/media/migration-sd
```

Restore requires the same eMMC identity, a complete backup and valid checksums.
It **removes all existing contents of the selected partition**, restores the
archive, unmounts and compares persisted data read-only. It does not reboot.
The new layout uses partition 4, but Linux MMC numbering varies; confirm it
against `/dev/emmc`. Preserve the SD backup until the migrated device is verified.
Restoring the entire old `/data` also restores old application/configuration
state; check its compatibility with the new OS. Backups contain credentials.

## Boot from SD (mmc1)

Prepare SD partition 1 as FAT32, starting at 8 MiB or later. Copy
`recovery.itb` and the complete `image/` directory to its root. U-Boot uses
`mmc 1:1`; Linux finds the SD card by its sysfs card type, not by assuming
Linux uses the same MMC numbering. The manual mount helper waits up to 30 seconds for the
card/partition device. For FIT boot through the existing U-Boot, an unpartitioned
card containing a whole-card FAT filesystem is also accepted; do not write raw
imx-boot into such a filesystem. Use the partitioned layout below for ROM SD boot.

Startup starts DHCP on `eth0` and TEZI over VNC (port 5900), and returns immediately to the
serial password prompt. TEZI discovers `image/image.json` on local SD media. There is no
HTTP server, feed registration, automatic backup, installation, restore or reboot.
The SD card is left unmounted for TEZI to probe and mount on demand. For manual
backup/restore, `/migration/mount-sd.sh` mounts it read/write at
`/run/media/migration-sd`. The UI starts even if the SD card is missing. Inspect
`/run/migration/dhcp.log`, `startup.log`, `weston.log` and `tezi.log` for diagnostics.

If eMMC already contains the signed U-Boot with the fixed recovery patch,
inserting this card makes it load `/recovery.itb#recovery` before environment
boot policy. No unsigned boot script or interactive CLI is needed.

### Boot the initial imx-boot from SDCard - Not verified!

Place SOM in recovery mode.
To boot the initial imx-boot from SD on CPU01, obtain the matching
HAB-signed bootloader separately from the secure Yocto build and write it
into the unpartitioned area at **33 KiB**, preserving the partition table.
For example, with `/dev/sdX` replaced by the verified SD device:

```sh
sudo dd if=/path/to/imx-boot-signed of=/dev/sdX bs=1024 seek=33 conv=notrunc,fsync
```

This offset is CPU01-specific (CPU01plus uses 32 KiB). Ensure the bootloader
fits before partition 1. The board's boot source must select SD for ROM boot;
inserting a card alone does not change the fused/strap boot source. A closed
device requires a matching HAB-signed bootloader whether loaded from SD or eMMC.

The DTB is given the exact `required-bootargs` expected by the recovery patch;
the FIT configuration is named `recovery`. Rootfs A/B signing and dm-verity
inside the installed OS payload remain properties of that supplied payload.

## Boot from the legacy /data partition (no SD card)

Many SD cards do not work with the legacy bootloader. This variant needs no
removable media: `recovery.itb` embeds the payload and is loaded by the legacy
U-Boot from the old `/data` partition. After boot, the installation runs
**without operator interaction** and erases the complete eMMC, including `/data`.

**The customer backs up `/data` before migration and restores what is needed
afterwards.** This variant has no backup, restore or SD tools. The new system
starts with the payload's empty data partition.

### Build

```sh
python3 migration/build-data-recovery.py \
  --platform cpu01 \
  --tezi migration/tezi/Verdin-iMX8MM_ToradexEasyInstaller_7.7.0+build.13 \
  --dtb /path/to/imx8mm-verdin-wifi-moducop-cpu01.dtb \
  --root-password-hash-file /path/to/recovery-root.hash \
  --payload /path/to/standard-image.mender_tezi.tar \
  --fit-keydir /path/to/staging-keys --fit-keyname dev \
  --output gen/cpu01-data-recovery
```

Inputs and signing options match the recovery build. The legacy devices are
open, so `--allow-unsigned` boots too. The payload is validated as in
`build-payload.py` and embedded in the gzip squashfs RAM root at
`/migration/image`. Squashfs compresses the raw images, and TEZI reads them
uncompressed. The FIT hashes (and signature, if signed) therefore cover the
payload. `build.json` records the payload archive's SHA-256.

Output: `recovery.itb`, `data.scr` (source in `data.cmd`), `build.json` and
`SHA256SUMS`. A staging CPU01 payload gives a ~360 MiB FIT. `ramdisk_size` is
derived from the squashfs size and written to both `data.scr` and the DTB's
`required-bootargs`. It is not the value pinned for the SD recovery patch.
The 256 MiB SD FIT limit does not apply. Instead, the build checks that the FIT
loaded at `0x60000000` and the ramdisk U-Boot relocates below `0x98000000` do
not overlap. That allows roughly 430 MiB of squashfs.

### Stage on the legacy device

Requires ~370 MiB free on `/data`. As root on the legacy OS:

```sh
findmnt -no SOURCE /data          # e.g. /dev/mmcblk2p4 -> partition 4
mkdir -p /data/migration
cp recovery.itb data.scr SHA256SUMS /data/migration/
cd /data/migration && grep -E ' (recovery\.itb|data\.scr)$' SHA256SUMS | sha256sum -c
sync
```

The files must be at `/migration/` on the `/data` filesystem, which is
`/data/migration/` in Linux. Set `migration_part` to `<emmc>:<partition>`.
Legacy U-Boot numbers eMMC as mmc `0` (SD is `1`), and the partition number is
the Linux `p<N>` suffix. If unsure, check `mmc list` and
`ls mmc 0:4 /migration` on the U-Boot console.

Do not stage while a Mender deployment is pending.

### Boot recovery

From the U-Boot serial console (one-shot, nothing is saved):

```
setenv migration_part 0:4
load mmc ${migration_part} 0x5ff00000 /migration/data.scr && source 0x5ff00000
```

Without a console, prepend the loader to the legacy `bootcmd` from Linux and reboot:

```sh
fw_printenv -n bootcmd > /data/migration/bootcmd.orig
fw_setenv migration_part 0:4
fw_setenv bootcmd "load mmc \${migration_part} 0x5ff00000 /migration/data.scr && source 0x5ff00000; $(cat /data/migration/bootcmd.orig)"
reboot
```

If loading or booting fails, U-Boot continues with the original `bootcmd`, and
the legacy OS boots unchanged. To cancel before rebooting, run
`fw_setenv bootcmd "$(cat /data/migration/bootcmd.orig)"` and
`fw_setenv migration_part`. A successful installation replaces the complete
U-Boot environment, so there is nothing to revert afterwards.

### Unattended installation

Recovery boots into RAM, opens the password-protected serial console, and starts
DHCP and the TEZI UI (VNC port 5900, useful for watching progress). Then
`/migration/install-embedded.sh`:

1. Verifies the embedded payload checksums.
2. Serves `/migration/image` read-only on TEZI's USB NCM address
   `192.168.11.1:8765`. A connected USB host can read the payload.
3. Waits for TEZI to be idle, then runs
   `tezictl image-install http://192.168.11.1:8765/image.json`.
4. Reboots with `reboot -f` only after TEZI reports `installed`.

TEZI still checks the payload's product IDs. On failure the device stays in
recovery and does not reboot. The eMMC may already be erased, and recovery
would not survive a power cycle. Keep the device powered and inspect
`/run/migration/install.log`, `tezictl.log`, `http.log` and
`/var/volatile/tezi.log` from the serial shell. Rerun
`/migration/install-embedded.sh` once the cause is fixed. A device whose eMMC
was erased and which was then powered off needs the module's USB recovery mode.

### Not yet validated on hardware

- Legacy U-Boot reading a ~360 MiB file from the old ext3/ext4 `/data`. Older
  U-Boot versions do not support every ext4 feature, and a failed load falls
  back to the legacy OS.
- `bootm` relocation of the ramdisk with the legacy environment.
- TEZI 7.7 creating `usb0` and accepting `tezictl image-install` with an explicit
  URL, without a UI confirmation.
- `reboot -f` into the newly installed system.

## Validation

```sh
python3 -m unittest discover -s migration/tests -v
for script in migration/runtime/*.sh migration/runtime/rc.local; do sh -n "$script"; done
```

Host tests cover payload validation, the signed FIT contract, SD discovery and
full-data backup/restore with simulated mounts and real tar/checksums. Hardware
qualification is still required on both platforms: DHCP with late cable insertion,
serial shell, VNC/local image discovery, SD backup space and write failures,
large FAT32 backups, manual install/restore, signed boot, unchanged fuses,
Mender authentication and subsequent A/B OTA. No generated image is a
hardware-qualified release.
