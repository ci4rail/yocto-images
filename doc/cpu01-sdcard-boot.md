# Moducop CPU01: prepare and boot a recovery SD card

Use the intact U-Boot on eMMC to load the kernel, CPU01 device tree, and root
filesystem from SD. The card contains an ext4 rootfs mounted read-only and a
second ext4 partition for writable `/data` and the `/etc` overlay.

This procedure applies to CPU01 with Verdin iMX8M Mini.

## Prepare the SD card from the build host

Use [prepare-cpu01-recovery-sd.sh](../scripts/prepare-cpu01-recovery-sd.sh) with
the target's IP address. Prerequisites:

- A completed `cpu01-standard-image` build with matching
  `Standard-Image-moducop-cpu01.ext4` and
  `Standard-Image-moducop-cpu01.dataimg` outputs in
  `cpu01-standard-image/install/images/moducop-cpu01`.
- The target running from eMMC, with `/data` on eMMC and root SSH access.
- An SD card of at least 8 GB inserted in the target as `/dev/mmcblk1`.

Run from the repository root on the build host:

```bash
# Check local images and target prerequisites without writing anything.
./scripts/prepare-cpu01-recovery-sd.sh --check 192.168.24.11

# Transfer the images and prepare the recovery card.
./scripts/prepare-cpu01-recovery-sd.sh 192.168.24.11
```

**Preparation erases all contents of the SD card.** Enter `ERASE` when prompted.
SSH prompts for the root password as needed; the lab standard-image password is
`cheesebread`. SSH key authentication is also supported.

The script compresses and transfers the images, verifies their checksums,
creates a 4 GiB ext4 root partition and an ext4 data partition using the remaining
space, writes and enlarges both filesystems, and adapts the SD copy for recovery.
It points `/data` and the `/etc` overlay at the SD data partition and masks the
Mender update and resize services configured for eMMC.

The script refuses to overwrite a card being used as the running rootfs or data
filesystem. If the target is running from SD, reboot normally into eMMC before
preparing the card. Preparation leaves the target running from eMMC.

Additional options:

```bash
# Skip the SD erasure confirmation; SSH authentication is still required.
./scripts/prepare-cpu01-recovery-sd.sh --yes 192.168.24.11

# Use images from another deploy directory.
./scripts/prepare-cpu01-recovery-sd.sh --images /path/to/moducop-cpu01 192.168.24.11

./scripts/prepare-cpu01-recovery-sd.sh --help
```

Temporary staging files on the target are removed after success. If installation
fails, the script retains them for diagnosis and attempts to unmount the
filesystems it mounted.

## Boot from SD using U-Boot

Open the serial console. In the lab:

```bash
ssh 192.168.24.10
picocom /dev/ttyUSB0 -b 115200
```

Leave the console open and reboot the CPU01 from its Linux shell:

```sh
sync
reboot
```

Press a key when U-Boot displays `Hit any key to stop autoboot`.
At the **U-Boot prompt**, run the card inspection commands first. Then copy and
paste the combined boot command line:

```text
mmc list
mmc dev 1
mmc rescan
part list mmc 1
ext4ls mmc 1:1 /boot
ext4ls mmc 1:1 /boot/freescale

setenv kernel_addr_r 0x48200000; setenv fdt_addr_r 0x50200000; setenv kernel_comp_addr_r 0x40200000; setenv kernel_comp_size 0x08000000; part uuid mmc 1:1 sd_root_uuid; setenv bootargs console=ttymxc0,115200 root=PARTUUID=${sd_root_uuid} rootfstype=ext4 rootwait ro; ext4load mmc 1:1 ${kernel_addr_r} /boot/Image.gz; ext4load mmc 1:1 ${fdt_addr_r} /boot/freescale/imx8mm-verdin-wifi-moducop-cpu01.dtb; booti ${kernel_addr_r} - ${fdt_addr_r}
```

The combined line sets the boot parameters, loads the kernel and device tree,
and starts Linux. Both `ext4load` commands must succeed for the boot to work.
`mmc 1:1` selects the SD card's first partition. `part uuid` supplies its
partition UUID for the kernel's `root=` argument. The compression settings allow
U-Boot to decompress `Image.gz`; `-` specifies that no initramfs is used.

These commands load both boot files directly from SD. Keep the changes temporary
by omitting `saveenv`; a normal reboot then returns to the saved eMMC boot
procedure.

## Verify the SD boot

Log in as `root` on the serial console with password `cheesebread`:

```sh
stty rows 40 cols 120
uname -r
cat /proc/cmdline
findmnt -rn -o SOURCE,FSTYPE,OPTIONS /
findmnt -rn -o SOURCE,FSTYPE,OPTIONS /data
findmnt -rn -o SOURCE,FSTYPE,OPTIONS /etc
```

Expected mounts:

- `/`: `/dev/mmcblk1p1`, ext4, read-only.
- `/data`: `/dev/mmcblk1p2`, ext4, writable.
- `/etc`: overlay with upper/work directories under `/data/overlay-etc`.

The lab card obtained `192.168.24.11` via DHCP and supported SSH login:

```bash
ssh root@192.168.24.11
```

The fresh recovery system has its own SSH host key. If SSH reports a changed key,
verify its fingerprint via serial before trusting it:

```sh
dropbearkey -y -f /etc/dropbear/dropbear_rsa_host_key
```
