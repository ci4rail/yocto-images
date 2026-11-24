# Migrating a ModuCop from image 2.x to 3.x

<!--
  TODO (maintainers) before publishing:
  - Name the 2.x releases that are supported as a starting point.
  - State which bundle customers should use (Non-Secure-Boot or secure-production).
  - CPU01plus has not been validated yet (see migration/README.md "Hardware validation").
  - Step 7 (Mender), verify on hardware:
    - Does the 3.x payload ship /data/mender/mender.conf, and with which content?
    - Does a freshly migrated device have a factory layer (e.g. ci4rail user files)?
    - Does the device show up as a new pending auth set for the existing device
      (same MAC-based identity), or as a new device?
    - Where did 2.x customers keep TenantToken (/data or /etc mender.conf)?
    - How do customers obtain the delegation signature (contact/process)?
-->

This guide explains how to upgrade a ModuCop CPU01 or CPU01Plus that runs a
2.x image to a 3.x image. No SD card and no installer PC are needed: you copy a
migration package to the device's `/data` partition, restart the device once,
and it installs the new image on its own.

Image 3.x uses a different partition layout from 2.x, so this is not a normal
OTA update. **The migration erases the complete internal storage (eMMC),
including `/data`.** All containers, application data, configuration and
credentials stored on the device are lost unless you back them up first.

## Overview

1. Back up everything you need from `/data`.
2. Download the migration package for your device.
3. Copy the package to `/data/migration` on the device.
4. Start the migration from the U-Boot console or from Linux.
5. Wait for the device to install 3.x and restart (do not power it off).
6. Set up the device again and restore your data.
7. Set up Mender: install your customer OTA key, connect to your Mender server
   and deploy your applications again.

## What you need

- A ModuCop CPU01 or CPU01Plus that runs a 2.x image and boots normally.
- Root access to the device (SSH or serial console).
- About 370 MiB free space on `/data`.
- Somewhere to store your backup (a PC or a network share), in case you want to backup your `/data` contents.
- A stable power supply. **Losing power during step 5 can leave the device
  unable to boot**; it then has to be returned for repair.
- Optional, but recommended: a serial console cable. It lets you watch progress
  and fix problems if anything goes wrong.

## Step 1: Back up your data

The migration does not back up or restore anything. Copy everything you want to
keep from the device before you start, for example:

```sh
# On your PC: archive /data from the device over SSH
ssh root@<device> 'tar -C /data -czf - .' > device-data-backup.tar.gz
```

Typical items to keep:

- Your application data and Docker Compose files.
- Network and other configuration you changed on the device.
- Your Mender connection settings: `TenantToken` and, if you do not use
  hosted Mender, `ServerURL` or `Servers`. They are usually in
  `/data/mender/mender.conf` or `/etc/mender/mender.conf`.

Check that the backup is complete and readable before you go on.

> **Do not simply restore the whole backup onto the 3.x system.** Configuration
> that was valid on 2.x can be wrong on 3.x. In particular, an old
> `mender.conf` is rejected by the new updater. Restore only your application
> data and set up the configuration again (see [Step 6](#step-6-set-up-the-device-again)).

## Step 2: Download the migration package

Download the migration package for your device and the 3.x version you want from
the GitHub release:

| Device     | Package file name                                                       |
|------------|-------------------------------------------------------------------------|
| CPU01      | `Moducop-CPU01_Standard-Image_<version>-secure-production-data-migration.tar` |
| CPU01Plus  | `Moducop-CPU01Plus_Standard-Image_<version>-secure-production-data-migration.tar` |

Make sure the package matches your device type. The installer refuses an image
for the wrong device type, but by then the eMMC may already be erased.

## Step 3: Copy the package to the device

Copy the package to the device, for example with `scp`:

```sh
scp <package>.tar root@<device>:/data/
```

Then, as root on the device:

```sh
# Check that /data has enough free space (at least ~370 MiB)
df -h /data

# Find the partition that holds /data, e.g. /dev/mmcblk2p4 -> partition 4
findmnt -no SOURCE /data

# Unpack the package into /data/migration and check it
mkdir -p /data/migration
tar -C /data/migration -xf /data/<package>.tar
cd /data/migration
grep -E ' (recovery\.itb|data\.scr)$' SHA256SUMS | sha256sum -c
sync
```

The check must print `OK` for both `recovery.itb` and `data.scr`. If it does
not, delete `/data/migration` and download the package again.

Write down the partition number shown by `findmnt` (the number after `p`; in the
example above it is `4`). You need it in the next step.

## Step 4: Start the migration

Choose **one** of the two options. In both, replace `0:4` with `0:<partition
number>` from Step 3. The `0` is the internal eMMC.

### Option A: from the U-Boot serial console

Connect the serial console, restart the device and press a key to stop in
U-Boot. Then enter:

```
setenv migration_part 0:4
load mmc ${migration_part} 0x5ff00000 /migration/data.scr && source 0x5ff00000
```

Nothing is saved: if you restart without entering these commands, the device
boots 2.x as usual.

### Option B: from Linux (no serial console needed)

As root on the device:

```sh
fw_printenv -n bootcmd > /data/migration/bootcmd.orig
fw_setenv migration_part 0:4
fw_setenv bootcmd "load mmc \${migration_part} 0x5ff00000 /migration/data.scr && source 0x5ff00000; $(cat /data/migration/bootcmd.orig)"
reboot
```

If you change your mind **before** rebooting, undo the change with:

```sh
fw_setenv bootcmd "$(cat /data/migration/bootcmd.orig)"
fw_setenv migration_part
```

If the migration files cannot be loaded, the device simply boots 2.x again.

## Step 5: Wait for the installation

After the restart, the device runs the installer from memory and installs 3.x
without any input. On the serial console you see messages starting with
`Migration:`, ending with:

```
Migration: Installation complete. Rebooting into the new system in 10 seconds.
```

The device then restarts into 3.x.

**Keep the device powered the whole time.** Once the installation has started,
the eMMC is erased; a power loss at this point leaves a device that needs USB
recovery mode.

Optionally, you can watch the installer on its VNC screen: connect a VNC viewer
to the device's IP address (assigned by DHCP on the first Ethernet port), port
5900.

If the installation fails, the device does **not** restart. It stays in the
installer and shows the reason on the serial console. Keep it powered and see
[If something goes wrong](#if-something-goes-wrong).

## Step 6: Set up the device again

The device now runs 3.x with an empty `/data` partition. Everything from 2.x is
gone, including the `/data/migration` files; nothing needs to be cleaned up.

1. Log in and check that the device runs the new version.
2. Set up network and other configuration again for 3.x. Do not copy old
   configuration files over unchanged.
3. Set up Mender as described in [Step 7](#step-7-set-up-mender).
4. Restore your application data and Docker Compose files from your backup.
5. Check that your applications run.

Keep your backup until you have confirmed that the device works correctly.

## Step 7: Set up Mender

Mender works differently in 3.x:

- **Every update must be signed.** 3.x only installs Mender artifacts (OS,
  application and os-customization updates) that are signed either by Ci4Rail
  or by your own customer key. Unsigned artifacts that worked on 2.x are
  rejected.
- **Mender does nothing until your customer key is installed.** Without it,
  the updater refuses *every* update, including updates signed by Ci4Rail.
  You therefore have to install the key on the device itself (7.2); it cannot
  be delivered through Mender.
- **The device has a new Mender identity key.** The old key was stored on
  `/data` and was erased, so the Mender server sees a new authentication
  request from the device.

Carry out the steps below in this order.

### 7.1 Get your customer key signed (once, not per device)

You need this only once for all your devices. If you already have a signed key
for 3.x, go to 7.2.

1. Create an RSA key pair of at least 3072 bits:

   ```sh
   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out customer-artifact.key.pem
   openssl pkey -in customer-artifact.key.pem -pubout -out customer-artifact-pub.pem
   ```

   Keep `customer-artifact.key.pem` secret and **never copy it to a device**.
   You use it to sign your Mender artifacts.

2. Send `customer-artifact-pub.pem` (only the public key) to Ci4Rail. You get
   back the signature file `customer-artifact-pub.pem.sig`.

Do not edit `customer-artifact-pub.pem` after you have sent it (not even its
line endings), or the signature no longer matches.

### 7.2 Install the customer key on the device

Install the key in the os-customization **factory** layer. This way it
survives a factory reset. Copy `customer-artifact-pub.pem` and
`customer-artifact-pub.pem.sig` to the device, then, as root:

```sh
# Start from the current factory layer, if there is one: install-factory replaces it
mkdir -p /data/ota-setup/etc/ota
if [ -d /data/os-customization/factory ]; then
    cp -a /data/os-customization/factory/. /data/ota-setup/
fi

cp customer-artifact-pub.pem customer-artifact-pub.pem.sig /data/ota-setup/etc/ota/
[ -f /data/ota-setup/manifest.json ] ||
    echo '{"format_version": 1, "version": "customer-ota-keys-1"}' > /data/ota-setup/manifest.json

os-customization-set install-factory /data/ota-setup
rm -rf /data/ota-setup
reboot
```

Both files must be regular files, not symbolic links. After the reboot,
check that they are in place:

```sh
ls -l /etc/ota/
```

### 7.3 Connect the device to your Mender server

Enter your Mender connection settings in `/data/mender/mender.conf`. Only
these settings are allowed there:

- `TenantToken`
- `ServerURL` or `Servers` (only needed if you do not use `https://hosted.mender.io`)
- `UpdatePollIntervalSeconds`, `InventoryPollIntervalSeconds`,
  `RetryPollIntervalSeconds`

Any other setting, for example an old `ArtifactVerifyKey`, makes the updater
refuse all updates. So **do not copy the old `mender.conf` from your backup**;
take only the values listed above from it. Example:

```json
{
  "TenantToken": "<your tenant token>"
}
```

Then restart Mender:

```sh
systemctl restart mender-authd mender-updated
```

### 7.4 Accept the device in Mender

Because the device has a new identity key, it appears in the Mender UI as
**pending**. Accept it under *Devices*. If the old entry of the device is still
listed, you can remove it.

### 7.5 Deploy your applications again

The device starts without any applications or os-customizations. Sign your
application and os-customization artifacts with your customer key and deploy
them again:

```sh
mender-artifact sign -k customer-artifact.key.pem my-application.mender
```

Artifacts you used on 2.x must be signed before you can deploy them to 3.x.
For OS updates, use only the signed 3.x Mender artifacts from the Ci4Rail
release; 2.x OS artifacts cannot be installed.

### 7.6 Check Mender

Deploy a signed artifact, for example your application, and check that it
installs. If updates fail, the reason is in the log, marked with
`OTA protection:`:

```sh
journalctl -u mender-updated | grep "OTA protection:"
```

After fixing the cause, restart Mender with
`systemctl restart mender-updated`. If systemd stopped restarting the
service, run `systemctl reset-failed mender-updated` first.

## If something goes wrong

| Symptom | Cause and solution |
|---|---|
| Device boots 2.x again after Step 4 | The migration files could not be loaded. Check the partition number and that the files are in `/data/migration`, then repeat Step 4. Some older bootloaders cannot read every ext4 feature; in that case contact Ci4Rail support. |
| Installer stops with an error and does not restart | Keep the device powered. Contact Ci4Rail support and have the serial console ready; the logs are in `/run/migration/` and `/var/volatile/tezi.log` on the installer system. |
| Device does not boot at all after a power loss during Step 5 | The eMMC was erased. The device must be reinstalled. Contact Ci4Rail support. |

The installer's serial console asks for a root password. This is for Ci4Rail
support only; you do not need it for a normal migration.
