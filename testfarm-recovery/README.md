# Testfarm USB recovery bundles

This build supplies the signed boot chain needed to load TEZI into RAM on closed
CPU01/CPU01Plus devices through USB ROM recovery, including devices with erased
eMMC. It is separate from the installed OS and from the manual SD migration
workflow. No migration files or behavior are changed.

The bundle contains a dedicated HAB-signed bootloader and the existing
FIT-signed `recovery.itb`, built using the migration builder without modification.
The OS `.mender_tezi.tar` payload remains a separate input to `toradex_installer`.
This change supplies the bundle only: installer selection, authenticated serial
login, autoinstall payload preparation and completion/reboot handling still need
to be adapted in `toradex_installer`. The bundled runtime retains its existing
password-protected serial shell and manual TEZI behavior.

## Build in CI

Run **Build testfarm USB recovery bundle** (`testfarm-recovery.yaml`) with:

- `platform`: `cpu01` or `cpu01plus`.
- `signing_mode`: `staging` or `production`.
- `minio_bucket`: an existing private bucket on `minio.ci4rail.com`.

The same workflow can be called with `workflow_call`; outputs are `object_uri`
and `sha256`. It has no push, release or pull-request trigger. It does not flash
hardware, change fuses, publish GitHub artifacts or modify the existing image CI.

Supported combinations:

| Platform | Staging | Production |
|---|---|---|
| CPU01 | Existing dummy HAB and FIT keys | Existing CPU01 Azure HAB and FIT keys |
| CPU01Plus | Same dummy HAB and FIT keys | Rejected: no reviewed production key configuration |

A closed staging device must have the matching dummy HAB trust anchor fused.
Staging bundles cannot boot on production-fused devices. CPU01Plus production
requires its own reviewed HAB certificates/key IDs and FIT signing configuration;
do not reuse CPU01 production keys implicitly.

Uses the existing `staging` and `production` GitHub environments. Configure:

- `RECOVERY_ROOT_PASSWORD_HASH`: SHA-512 crypt hash for the existing runtime.
- `RECOVERY_MINIO_ACCESS_KEY` and `RECOVERY_MINIO_SECRET_KEY`: private-bucket upload
  credentials (environment secrets or reusable-workflow secrets).
- Production environment variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and
  `AZURE_SUBSCRIPTION_ID`, with GitHub OIDC federation configured as for OS builds.
  Unlike the migration runtime-only build, this also requires HAB CSF/IMG signing
  and certificate access, in addition to FIT signing.

The runner uses the existing `yocto-runner` label and kas 4.7. Bootloader layers
are pinned; the signing layer matches the migration production tooling pin.
Downloads and sstate are mounted directly from the runner's persistent
`/yocto-cache/downloads` and `/yocto-cache/sstate-cache` directories, reusing
compatible entries from previous recovery and OS builds. BitBake manages cache
locking; no per-job cache copy is needed. Completed cache entries survive failed
builds as well. The cache volume must be writable by the runner and visible at
the same path to its Docker daemon; the workflow fails if it is unavailable.
Build outputs and signing work directories remain temporary and are cleaned up
after each job; the persistent caches are retained. No
production private keys are downloaded to the runner or bundled. The uploader
requires `s3:GetBucketPolicy` and `s3:PutObject` on its destination, and rejects
anonymous bucket access. It does not create buckets or change policies.

Outputs:

```text
s3://<bucket>/testfarm-recovery/<platform>/<mode>/<version>/<run>-<attempt>/
    recovery-bundle.tar.gz
    recovery-bundle.tar.gz.sha256
```

Archive contents:

```text
imx-boot-recovery        HAB-signed SPL/U-Boot container for USB recovery
recovery.itb             Existing signed RAM-resident TEZI runtime
recovery/uuu.auto        Platform-specific USB loading sequence
u-boot.dtb              Bootloader verification DTB (public key)
u-boot.config           Compiled bootloader configuration
srk-fuse.bin            Expected HAB SRK digest (reference only, never programmed)
runtime-build.json      Existing runtime provenance
build.json              Bundle identity, layer revisions, keys and file hashes
SHA256SUMS              Checksums of all other archive files
```

Checksums detect corruption; obtain the bundle and its identity through trusted
CI/storage. Packaging verifies the recovery FIT against the DTB exported from
the bootloader build, checks required hardening settings and presence of both
HAB CSFs in the signed container. These CSF checks are not independent HAB
cryptographic verification: ROM/SPL authentication must be qualified on hardware.
The manifest explicitly records `hardware_qualified: false`.

## Boot protocol

After putting the device into USB ROM recovery, run UUU 1.4.127 from the
extracted bundle root:

```sh
uuu recovery
```

CPU01 uses SDP followed by SPL's SDPV transfer; CPU01Plus uses SDPS. The dedicated
bootloader enters Fastboot before environment boot policy, SD discovery or the
U-Boot CLI. It resets environment values to compiled defaults, downloads into a
fixed 256 MiB buffer at `0x60000000`, and accepts only `getvar`, `download` and
`continue`. `continue` requires a completed download, exits Fastboot, then boots
`0x60000000#recovery` using the existing recovery boot arguments. Toradex FIT,
bootargs and load protections remain enabled. Boot failure stops execution.

There is no unsigned `boot.scr`, `UCmd`/`ACmd`, raw Fastboot flashing, fuse
programming or eMMC installation in this bootloader. Installation is performed
by TEZI after the signed runtime has started. The recovery bootloader is a RAM
loader; do not install it as the normal eMMC bootloader.

## Validation

```sh
python3 -m unittest discover -s testfarm-recovery/tests -v
```

Before connecting this workflow to unattended testfarm flashing, qualify CPU01
and CPU01Plus separately: closed device with matching keys, mismatched keys,
corrupt FIT, erased eMMC, USB reconnect/failure, forbidden Fastboot commands,
USB gadget payload discovery, unchanged fuse values and installed OS boot.
A successful UUU transfer alone is not proof that the installed OS boots.
