# ModuCop target integration tests

Pytest ports of `ci4rail/cpu01-yocto-testcases`, branch `yoto-security-tests`
(commit `942941f106fc4b254349d999958ac9b8498dcf7b`). Pure Python security
parsers originate from that revision. Container/storage/serial helpers were
ported from `ci4rail/tc-lib` branch `yocto-scarthgap`; flashing follows
`tc-lib` commit `c16e1d0c68bdd802066f9f09f58aebd12901bd9c`.

Supports CPU01 (`verdin-imx8mm`) and CPU01Plus (`verdin-imx8mp`) standard
images. Currently use open devices and `ci4rail/tdx-installer-dev:v3`.
For closed devices, set `closed: true` and supply a suitable signed installer;
v3 is rejected. No test changes fuses or proves secure-boot enforcement.

## Run on the TC

Copy `config/station.example.yaml` to `config/station.yaml`, edit the station
and platform details, and provide environment variables referenced by YAML.
Set `tc.local: true`. Credentials stay in local configuration/environment.
`TARGET_IP` must be the actual reachable address after flashing; hostname
resolution also works. DHCP discovery is not performed. CPU01Plus needs its
own hardware paths/device names where they differ from the CPU01 example.

```sh
export TARGET_IP=192.168.24.65
# Set TARGET_PASSWORD, WIFI_SSID, WIFI_PASSWORD and DOCKER_PASSWORD in your local environment.
./run.sh --station config/station.yaml --image /path/to/image.tezi.tar
```

`run.sh` creates `.venv`, installs the pinned Python requirements, then runs
pytest through that venv. If `tests/.env` exists, `run.sh` sources it and
exports its variables to pytest. It passes `-s` so test progress printed to
stdout appears immediately; use `-s` with direct pytest invocations too.
For an already provisioned venv:

```sh
.venv/bin/python -m pytest --station config/station.yaml --image /path/to/image.tezi.tar
.venv/bin/python -m pytest --station config/station.yaml --skip-flash -k security
.venv/bin/python -m pytest --collect-only
.venv/bin/python -m pytest unit
```

Always run pytest from a virtual environment; the suite rejects system Python.
Default execution flashes once per session, erasing target eMMC, then releases
recovery, switches power off for two seconds and powers on before waiting for SSH. The SD-card
test overwrites the configured removable card, including its partition table.
Use `--skip-flash` to reuse a target. Remove absent hardware from `features`
to skip those tests. A declared feature with missing tools fails rather than
silently skipping. No hardware is accessed during collection or unit tests.
Do not use pytest-xdist: tests mutate one shared target.

The default logs and JUnit report from `run.sh` are in `results/`.
`--results` controls command-log placement; pytest's `--junitxml` controls XML.
JUnit test-case properties include iperf throughput in Mbit/s, fio read/write
throughput in bytes/s, and LTE packet loss and mean RTT when measured.
Sensitive command output is excluded from command logs. Do not enable pytest
local-variable dumps on runs containing credentials.

## Run from the development computer

Set `tc.local: false` and configure `tc.host`. By default SSH uses the current
user, SSH config, agent, and standard key files. Override `tc.username`,
`tc.key_file`, or `tc.port` if needed. The TC must already be in known_hosts.
Paramiko supports HostName/User/Port/IdentityFile/ProxyCommand here; use an
explicit ProxyCommand if your setup normally uses ProxyJump.

Conversion runs on the pytest runner before copying the disk image to the TC.
Thus the DC also needs the conversion utilities and noninteractive sudo when
flashing. Alternatively run pytest directly on the TC. Target SSH accepts
new host keys as configured because flashing replaces them.

## Provisioning

TC utilities are deployed separately using Ansible; tests do not install OS
packages. The TC user needs noninteractive sudo, Docker access without sudo,
Python with venv support, iperf3, brickd, GitHub CLI, and serial/USB access.
Conversion requires Python 3, Bash, jq, parted, file, tar, util-linux (loop and
mount tools), e2fsprogs, findutils and coreutils. Use a local filesystem with
enough space for the extracted archive and a disk image about 1.5 times its size.

The installer receives `BRICKD_UID`, `BRICKD_IP`, and `BRICKD_PORT` from
station configuration. Its embedded configuration uses power relay 0 and
recovery relay 1. Other relay channels require a different installer
configuration. The harness requires the installer's explicit SUCCESS marker.

DUT prerequisites: SSH with configured password authentication, Docker and
Compose, iperf3, iproute2, udhcpc, NetworkManager/nmcli, ModemManager/mmcli,
iw, gpsd, lsof, nftables, avahi-browse and mDNS resolution, ttynvt, ping,
parted, sfdisk, mkfs.vfat, and ordinary shell/file utilities. Root privileges
are needed for storage, network namespaces and nftables. The GNSS test reads
gpsd JSON reports through the BusyBox container.

Container dependencies:

| Image | Host | Purpose |
|---|---|---|
| ci4rail/tdx-installer-dev:v3 | TC | Flashing |
| ci4rail/security-test-tools:latest | TC | nmap/NSE, nc, protocol abuse |
| ghcr.io/ci4rail/linux-testing-container:latest | DUT | fio and linux-serial-test |
| busybox:latest | DUT | Docker and gpsd smoke tests |
| nginx:alpine, alpine:latest | DUT | Compose networking |

All images running on TC/DUT must support ARM64. Preauthenticate the TC for
private pulls. The login test reads `docker_password` from station configuration
and logs the DUT into Docker Hub as `docker_username`. Environment references
such as `$DOCKER_PASSWORD` and `${DOCKER_PASSWORD}` are expanded on the pytest
runner (DC or TC), so set the variable there.
If the GHCR image is private, separately provision DUT GHCR authentication
(after each flash), or make that image anonymously pullable. Docker Hub
credentials do not authenticate GHCR. No registry credentials are bundled.
GitHub CLI must already be authenticated for release/artifact downloads; a
`TC_GH_TOKEN_FILE` path alone does not authenticate it.

The station lock is an atomic directory under `tc.work_dir`, shared by local
and remote executions. Normal exits release it. After a killed runner, check
that no test/installer is active before removing `station.lock`. All clients
sharing a physical station must use the same work directory.

## CI artifact

The reusable image workflow uploads `pytest-package-<machine>-<run id>` for
standard images. It contains source, requirements, scripts and example
configuration, never a virtual environment, credentials, or test results.
The TEZI image remains a separate build artifact. CI does not execute tests.

Download on the TC with an authenticated GitHub CLI, for example:

```sh
gh run download RUN_ID -R ci4rail/yocto-images-secure-boot -n pytest-package-cpu01-RUN_ID -D incoming
mkdir -p suite
tar -xzf incoming/yocto-pytest-package.tar.gz -C suite
cd suite
./run.sh --station /path/to/station.yaml --image /path/to/image.tezi.tar
```

Manual packaging: `tests/scripts/package.sh /path/to/yocto-pytest-package.tar.gz`.
The existing scheduler/MQTT/Tailscale infrastructure can invoke `run.sh` after
downloading; this change does not deploy or configure that separate service.

## Coverage and baseline

Ports cover SSH authentication, Docker/Compose, both Ethernet interfaces,
GNSS, LTE, eMMC, SD card, Wi-Fi, serial loopback, io4edge discovery, TCP/UDP
attack surface, Nmap vulnerability scripts, root network services, nftables
egress, and malformed TCP/UDP traffic. Persistence tests check journal rotation,
log survival across a reboot, a 100 MB `/var/log` usage limit, a 100 MB persistent
journal limit under more than 100 MB of random log input, and at least 10%
free root filesystem capacity. The journal test reboots the target. Serial tests require physical loopback;
GNSS requires reception; LTE requires an active SIM; Ethernet/Wi-Fi require
station connectivity. Thresholds remain 900/80/5 Mbit/s for ETH1/ETH2/Wi-Fi,
configured storage throughput, and LTE loss below 50% with mean RTT below
1500 ms. Security baseline values remain unchanged, including the existing
egress CIDR TODO. `max_cvss` is retained but unused by the upstream Nmap
string-based vulnerability test; this is not a CVSS-scored scanner.

## Application update module tests

`yocto_tests/test_application_module.py` exercises the installed `app` Update
Module and Docker Compose v2 helper with real containers. It calls Mender v3 states
with generated payload trees, without modifying the Mender deployment database
or contacting a deployment server. The target must have the rollback-capable
module installed. No automatic module installation or rootfs remount occurs in
the test suite.

```sh
.venv/bin/python -m pytest yocto_tests/test_application_module.py \
  --station config/station.yaml --skip-flash
```

The fixture uses `alpine:latest`, pulling it only if absent. Preload it to run
without registry access, or set `application_test_image` in station configuration
to an image with `/bin/sh`, `sleep`, `test`, and `touch`.
Tests build two small image variants without network access and use unique
Compose project names, tags and directories under `/data`. Cleanup removes
only these test resources; existing applications and volumes remain untouched.
The engine retains its normal build cache. Use the same station lock as other
hardware runs, and run sequentially.

Coverage includes commit/cleanup, reused image tags, first-install rollback,
manifest-only updates, corrupt images, malformed manifests, failures and process
termination during installation and rollback, recovery retry, missing backups,
ordinary delta payloads (including shared base tags), layer deltas, environment preservation,
unmanaged projects, and optional readiness checks.
Fault injection kills only the test module process at helper-call boundaries.
These checks do not simulate physical power loss or exercise the Mender client's
server-driven state machine. Output is captured in `<results>/dut.log`; direct
SSH invocations do not send module output to journald.

`yocto_tests/test_application_mender_update.py` separately exercises the installed
modules through the target's `mender-update` standalone client. It builds real
`.mender` Artifacts on the pytest runner, transfers them to the target,
and invokes `install`, `commit`, and `rollback`. Install/commit failures must
trigger the client's automatic rollback and cleanup. Coverage includes first
installation, committed updates, reused image tags, manifest-only updates,
explicit rollback and subsequent updates, corrupt images, invalid manifests,
container startup failure, and health rejection at install and commit.

Install `mender-artifact` on the pytest runner (DC or TC), or set
`mender_artifact_binary` in station YAML to its executable path. The target must
already contain `mender-update` and both rollback-capable app modules. Run with:

```bash
cd tests
.venv/bin/python -m pytest --station config/station.local.yaml --skip-flash \
  yocto_tests/test_application_mender_update.py
```

These tests invoke `mender-update` with the installed configuration, database,
state scripts, module paths, and app store. The updater service remains in its
existing state and can run alongside the tests. The normal database's artifact
name/provides change as updates are committed; run
on a dedicated test target with no update already in progress. Test containers,
tags, payload directories and their application manifests are removed afterward.

For targets that require signed Artifacts, set `mender_artifact_signing_key` in
station YAML to a private key file on the pytest runner whose public key the
target already trusts. The private key stays on the runner. Signature checks and
protected OTA policy remain enabled, including on `CI4RAIL_OTA_PROTECTION=1`
images. Without this setting, Artifacts are unsigned and require a target whose
installed policy accepts them.

Health rejection cases require the default `APP_HEALTHCHECK_ENABLED=yes` in the installed
`/etc/mender/mender-app.conf`; they skip when health checking is disabled. The
tests do not modify this configuration. This suite exercises client Artifact
parsing, signature/compatibility policy, state transitions, module execution and
cleanup. Server deployment/authentication and physical power loss remain outside
its coverage. Client output is captured in `<results>/dut.log`; the existing
application-module tests retain detailed fault-injection and delta coverage.

Docker 25's exports can contain current timestamps in tar headers, so repeated
`docker image save` calls need not produce identical archive bytes. The ordinary
binary-delta fixtures freeze the base export and verify its live image ID before
returning it through the helper's `SAVE` call. This tests reconstruction, tag
ordering and rollback without depending on archive timestamp coincidence. The
layer-delta case uses actual repeated Docker exports and stable layer bytes.
Production whole-archive deltas require a reproducible base archive; use full
image payloads or layer deltas when that condition cannot be met.

Health checking defaults to enabled in `/etc/mender/mender-app.conf`, with
`APP_HEALTHCHECK_ENABLED=yes`, `APP_HEALTHCHECK_TIMEOUT=60`, and
`APP_HEALTHCHECK_INTERVAL=2`. Set `APP_HEALTHCHECK_ENABLED=no` to disable readiness
checks. A service with a
container healthcheck must become healthy; a service without one must be
running. Intentional one-shot services must have the Compose label
`io.ci4rail.mender.oneshot: "true"` and exit successfully. Readiness is checked
at rollout and commit. The transaction keeps the configured settings across
separate module calls.

Rollback snapshots preserve resolved Compose configuration, image archives,
image IDs and tag mappings until `Cleanup`, including through `ArtifactCommit`.
Each service in the previous composition must still have a container (running
or stopped) so its exact deployed image can be saved. Missing containers or
mixed image versions within a scaled service cause installation to fail before
changing the running application. Inactive profiles and services scaled to zero
therefore need to be excluded from the managed composition. Persistent volume
contents and external bind-mount data are not rolled back; schema migrations
must remain compatible or supply their own recovery strategy. Reserve space for
image backups, incoming archives and delta working files. Images are not pruned
automatically because other applications may share them.

An unsuccessful rollback retains its transaction directory and per-application
lock under `/data/mender-app/.transactions`, preventing a later update from
destroying recovery data. Inspect the error and repair the underlying cause
before retrying `ArtifactRollback` with the original module file tree. If Mender
has already removed that tree, create a temporary file tree with `tmp/` and put
the retained transaction's absolute path in `tmp/app-transaction`; invoke the
installed module with `ArtifactRollback <tree>` and then `Cleanup <tree>` using
the same configuration. Do not remove the lock or backup to bypass a failed
recovery.
