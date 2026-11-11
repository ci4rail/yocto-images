# ModuCop Yocto target test cases

Summary of the hardware tests in `tests/yocto_tests`, in the order the CI runs
them (`tests/ci-run.sh`). The DUT is flashed once and every step reuses it, so
each step starts in the state the step before left it in.

## Test station

| Component | Role |
|---|---|
| DUT | ModuCop CPU01 (Verdin iMX8MM) or CPU01Plus (Verdin iMX8MP), secure boot closed, staging keys |
| TC (test computer) | Runs pytest, flashes the DUT via USB recovery (TEZI installer container), runs network tests (iperf3, nmap, SNMP client, SSH logins) |
| Relay (Tinkerforge) | Power and recovery lines of the DUT |
| Router (Ubiquiti) | DHCP for the testfarm LAN; hands out an NTP server via DHCP option 42 (`ntp_from_dhcp: true`) |

Session setup, done once per pytest run (fixtures in `conftest.py`, `setup.py`, `ota.py`):

* **Flash** (step 0 only): convert the staging TEZI image, install it through the
  signed recovery installer, power cycle and wait for SSH.
* **Factory OTA provisioning**: install the staging `customer-artifact-pub.pem` and its
  signature into the factory os-customization layer (`/etc/ota`) and reboot. Skipped if
  already present. Needed so the DUT accepts the test's signed Mender artifacts.
* **Profile** (`--profile <name>`): build the profile's os-customization payload into a
  signed Mender artifact and install it through the core API (reboots the DUT). Skipped
  if the same payload version is already installed.

---

## Step 0 – `0-flash-secureboot` (marker `secureboot`)

**DUT configuration:** freshly flashed closed (secure boot) staging image, factory OTA
keys provisioned, no os-customization. If this step fails, all further steps are skipped.

| Test | Description | Pass criteria |
|---|---|---|
| `test_station_uses_closed_device` | Checks the station is configured for a closed device | Station config has `closed: true` (skipped if `closed: false`) |
| `test_rootfs_is_dm_verity_protected` | Root filesystem is integrity protected | `/` is mounted from a `/dev/dm-*` device and the `dm_verity` module is loaded |
| `test_rootfs_is_read_only` | Root filesystem is immutable | `/` is mounted with option `ro` |

---

## Step 1 – `1-default` (all hardware tests except security, secureboot, profile, destructive)

**DUT configuration:** image defaults, no os-customization (root SSH login with password,
open egress, avahi and tailscaled active). Station features: CPU01 `ethernet2, gnss`;
CPU01Plus `gnss`. Tests for undeclared features (modem, wifi, serial, io4edge, sdcard)
are skipped.

| Area | Test | Description | Pass criteria |
|---|---|---|---|
| Access | `test_ssh_authentication` | SSH password logins with valid and invalid credentials (wrong user, wrong/empty/`root` password), key/agent fallback disabled | Only the configured user with the correct password is accepted |
| Docker | `test_docker_login` | `docker login` to Docker Hub on the DUT | Login succeeds |
| Docker | `test_busybox` | Run `busybox` container | Container runs and exits 0 |
| Docker | `test_compose` | Start `config/docker-compose.yaml` (nginx + client) | Client container receives the nginx welcome page within 60 s |
| GNSS | `test_gpsd_from_container` | Connect to gpsd (172.17.0.1:2947) from a container | gpsd answers with a `VERSION` report |
| GNSS | `test_gnss` | Watch gpsd JSON reports | 3D fix (`TPV`, mode ≥ 3, lat/lon) within 700 s |
| Network | `test_ethernet1` | iperf3 DUT ↔ TC on the main Ethernet | > 900 Mbit/s in both directions |
| Network | `test_ethernet2` | ETH2 moved to a network namespace, DHCP, iperf3 to TC | Throughput > `eth2_mbps` (80 Mbit/s) |
| Network | `test_modem` | LTE connection via NetworkManager, ping over modem | Within 300 s: packet loss < 50 %, mean RTT < 1500 ms |
| Network | `test_wifi` | Connect to configured WLAN, iperf3 to TC | > 5 Mbit/s |
| I/O | `test_serial` | ttynvt serial devices announced via mDNS, loopback test with `linux-serial-test` | All configured devices found, serial test passes without errors |
| I/O | `test_io4edge` | io4edge devices reachable | Configured IPs present and pingable, devices announced via mDNS and pingable by name |
| Storage | `test_emmc` | fio read/write on `/data` | `/data` is on eMMC; read/write bandwidth above configured minimum |
| Persistence | `test_persistent_logging` | Write journal marker, rotate, reboot | Marker readable from previous boot; `/var/log` ≤ 100 MiB + 2 MiB |
| Persistence | `test_journal_size_limit_under_load` | Write 4 × 32 MiB random data into the journal | All data accepted; journal disk usage ≤ 100 MiB |
| Persistence | `test_rootfs_spare_capacity` | Free space on rootfs | ≥ 10 % available |
| App module (direct) | `test_commit_keeps_backup_until_cleanup` | Install + commit new app version | Backup kept after commit, removed after Cleanup (idempotent); new version runs |
| App module (direct) | `test_rollback_restores_reused_tag_and_allows_next_update` [uncommitted, commit-leave-failure] | Rollback after install (and after commit) | Old image/tag and container restored; repeated rollback is a no-op; next update works |
| App module (direct) | `test_failed_image_load_leaves_old_containers_running` | Corrupt image archive | Install fails, old container keeps running unchanged |
| App module (direct) | `test_interrupted_install_recovers` [SNAPSHOT fail, LOAD/STOP/ROLLOUT killed] | Fault injection / kill during install phases | Rollback restores old version |
| App module (direct) | `test_failed_rollback_retains_backup_and_can_retry` | Injected restore failure | Backup retained, further installs blocked, retry rollback restores old version |
| App module (direct) | `test_first_install_rollback_removes_only_test_application` [started, invalid-manifest] | Rollback of a first install | App store entry and containers of the test app removed |
| App module (direct) | `test_manifest_only_update_can_roll_back` | Update without images | Rollback restores old version |
| App module (direct) | `test_optional_health_checks` [disabled, healthy, unhealthy, no-probe, oneshot-ok, oneshot-failed, exited-daemon] | Health check handling at install | Install succeeds/fails as expected per variant; rollback always restores old version |
| App module (direct) | `test_commit_rechecks_health` | Container becomes unhealthy after install | Commit fails, rollback restores old version |
| App module (direct) | `test_interrupted_rollback_can_retry` [STOP, RESTORE, ROLLOUT] | Kill during rollback phases | Second rollback restores old version |
| App module (direct) | `test_start_failure_rolls_back` | Container command does not exist | Install fails, rollback restores old version |
| App module (direct) | `test_healthcheck_waits_for_readiness` | Service becomes healthy after 5 s | Install waits and container is `healthy` |
| App module (direct) | `test_missing_previous_container_fails_before_loading_images` | Previous container deleted | Install fails before the image tag is changed |
| App module (direct) | `test_bad_metadata_rollback_is_noop` | `application_name` with path traversal | Install fails; rollback does not touch running container |
| App module (direct) | `test_rollback_without_image_archive_fails_and_keeps_evidence` | Backup archive missing | Rollback fails, transaction kept; succeeds after archive restored |
| App module (direct) | `test_delta_update_and_rollback` [valid-delta, invalid-delta] | xdelta3 image delta | Valid delta installs new version, invalid fails; rollback restores old |
| App module (direct) | `test_compose_environment_is_preserved_on_rollback` | Env values from metadata (spaces, `$`) | Correct env in new container and restored after rollback |
| App module (direct) | `test_paused_service_fails_readiness` | Container paused after install | Commit fails, rollback restores old version |
| App module (direct) | `test_unmanaged_project_is_not_replaced` | Foreign container with same compose project | Install fails, foreign container untouched and running |
| App module (direct) | `test_all_deltas_are_decoded_before_loading_moved_tags` | Two images, one delta against a moved tag | Both images installed correctly; rollback restores old |
| App module (direct) | `test_layer_delta_with_real_docker_exports` | Per-layer delta ("deep delta") | New version runs; rollback restores old |
| App via `mender-update` | `test_install_and_commit` | Signed app artifact installed via Mender client | New version runs and is reported by `show-artifact`; no update pending |
| App via `mender-update` | `test_explicit_rollback_and_next_update` [reused-tag, manifest-only] | `mender-update rollback` | Old image and artifact name restored; next update works |
| App via `mender-update` | `test_first_install_rollback` | Rollback of first install | App and its containers removed |
| App via `mender-update` | `test_install_failure_automatically_rolls_back` [corrupt-image, invalid-manifest, start-failure, unhealthy] | Faulty artifacts | Install exits 1, client rolls back automatically, old version and artifact name kept |
| App via `mender-update` | `test_commit_failure_automatically_rolls_back` | Unhealthy at commit | Commit exits 1, old version restored automatically |
| Core API | `test_core_api_updates` | Application (old → new), os-customization and CoreOS rootfs update via core API | Each update reports the expected current version; app and customization survive the CoreOS reboot. Deselected here; runs in step 5 |

---

## Step 2 – `2-internet-connected` (`--profile internet-connected`, markers `profile or security`)

**DUT configuration:** os-customization `internet-connected` installed via core API
(DUT rebooted). Ingress: SSH from `tailscale0` and the LAN 192.168.24.0/24, ICMP.
Egress open (Mender, Tailscale, internet). avahi/mDNS disabled, tailscaled active.
Root SSH login still allowed. Security baseline: TCP 22 only, no UDP ports,
no egress monitoring.

| Area | Test | Description | Pass criteria |
|---|---|---|---|
| Profile | `test_customization_installed` | Installed customization version | Core API reports the profile's payload version; `/etc/ci4rail-profile` = `internet-connected` |
| Profile | `test_services` | Service states | `tailscaled` active, `avahi-daemon` not active |
| Profile | `test_firewall` | Firewall table present | `nft list table inet ci4rail_fw` succeeds |
| Profile | `test_icmp_allowed` | Ping DUT from TC | Ping succeeds |
| Profile | `test_internet_reachability` | DNS and HTTPS from the DUT | Mender and Tailscale hosts resolve; Mender, Tailscale, example.com answer over HTTPS |
| Security | `test_attack_surface` [tcp, udp] | nmap port scan from TC | Open ports equal baseline: TCP `[22]`, UDP none |
| Security | `test_vulnerabilities` | nmap `vuln` NSE scripts on all open ports | No vulnerability or CVE reported |
| Security | `test_service_privileges` | Processes owning listening sockets (`lsof`) | Root-owned network services only from the allowed list |
| Security | `test_egress` | Egress monitoring | Skipped (profile does not restrict egress) |
| Security | `test_protocol_abuse` | Malformed TCP/UDP traffic to open ports | Open ports unchanged afterwards; SSH still reachable |

---

## Step 3 – `3-standalone` (`--profile standalone`, markers `profile or security`)

**DUT configuration:** before the customization, a stand-in customer container (nginx,
TLS, host network, port 443) is deployed as an application update. Then os-customization
`standalone` replaces `internet-connected` (DUT rebooted). Ingress: SSH, HTTPS 443,
SNMPv3 (UDP 161), ICMP. Egress: only DHCP, DNS and NTP client and replies.
tailscaled, avahi, snmptrapd and the Mender daemons disabled. SSH only as `moducop` or
`ci4rail` with key (no root, no passwords), sudo with password, no serial console login.
end0 has a static fallback address (192.168.250.1/24) while DHCP has no lease. The harness
logs in as `moducop` and runs commands through `sudo`.

| Area | Test | Description | Pass criteria |
|---|---|---|---|
| Profile | `test_customization_installed` | Installed customization version | Core API reports the profile's payload version; `/etc/ci4rail-profile` = `standalone` |
| Profile | `test_services` | Service states | `snmpd`, `ci4rail-end0-fallback` active; `tailscaled`, `avahi-daemon`, `snmptrapd`, `mender-updated`, `mender-authd`, `mender-connect` not active |
| Profile | `test_firewall` | Firewall table present | `inet ci4rail_fw` exists |
| Profile | `test_icmp_allowed` | Ping DUT from TC | Ping succeeds |
| Profile | `test_https_service` | Customer container over HTTPS | Reachable from TC on 443 with marker text; on DUT loopback `/` answers, unknown URL gives 404 |
| Profile | `test_snmpv3_authpriv_works` | SNMPv3 authPriv `get sysDescr.0` from TC | Answer contains a `STRING` |
| Profile | `test_snmp_v1_v2c_rejected` [public, private] | SNMP v1/v2c with community strings | No answer |
| Profile | `test_snmpv3_wrong_credentials_rejected` | Wrong auth/priv passphrase, unknown user | No answer |
| Profile | `test_snmpv3_without_privacy_rejected` | SNMPv3 authNoPriv | No answer |
| Profile | `test_ntp_client` | NTP server from DHCP (`ntp_from_dhcp`): option 42 → NetworkManager dispatcher → `/run/chrony-dhcp/*.sources` → chronyd | Sources file lists a server, `chronyc sources` shows it, `chronyc waitsync` succeeds and `chronyc tracking` references it. With only `ntp_server` set: one-shot `chronyd -Q` query |
| Profile | `test_client_egress_allowed` | Firewall ruleset content | Rules for DHCP (both directions), DNS (UDP/TCP 53) and NTP (UDP 123) present |
| Profile | `test_ssh_only_key_users` | SSH login matrix | Station root login rejected; each key user logs in with own key only; root rejects user keys; password login rejected |
| Profile | `test_sudo_with_password` | sudo for key users | `sudo -n` and wrong password fail; correct password gives root |
| Profile | `test_no_serial_console_login` | Gettys | No `serial-getty@` unit active, no getty on a serial tty |
| Profile | `test_end0_dhcp_with_static_fallback` | Block DHCP replies on end0, restart connection, then unblock | DUT reachable from TC on the fallback address without DHCP lease; DHCP keeps retrying; after unblock the DHCP address returns and the fallback is removed (power cycle on failure) |
| Security | `test_attack_surface` [tcp, udp] | nmap port scan from TC | Open ports: TCP `[22, 443]`, UDP `[161]` |
| Security | `test_vulnerabilities` | nmap `vuln` NSE scripts | No vulnerability or CVE reported |
| Security | `test_service_privileges` | Root-owned listening processes | Only `sshd`, `dropbear`, `systemd`, `snmpd`, `nginx` |
| Security | `test_egress` | Temporary nft output counters for 15 s | No packets outside LAN, DHCP broadcast, multicast, link-local and DNS/DHCP/NTP ports |
| Security | `test_protocol_abuse` | Malformed TCP (22, 443) and UDP (161) traffic | Open ports unchanged afterwards; SSH still reachable |

---

## Step 4 – `4-default-profile` (`--profile default`, marker `profile`)

**DUT configuration:** os-customization replaced by the empty `default` payload
(DUT rebooted), i.e. image defaults should be restored. The customer container from
step 3 remains installed (application update, not part of the customization).

| Test | Description | Pass criteria |
|---|---|---|
| `test_customization_installed` | Installed customization version | Core API reports the default payload version; `/etc/ci4rail-profile` = `default` |
| `test_services` | Service states | `tailscaled` active |
| `test_firewall` | Firewall table absent | `inet ci4rail_fw` does not exist |
| `test_default_has_no_leftovers` | Files of previous profiles removed | No `/etc/nftables.conf`, no example SNMP user, no tailscale/avahi/serial-getty disable drop-ins, no sudoers/authorized keys/password files for `moducop`/`ci4rail`, dropbear without extra args |

---

## Step 5 – `5-core-api-updates` (`--core-os-artifact`, only `test_core_api_updates`)

**DUT configuration:** default profile from step 4. Runs only when the bundle contains
`CORE_OS_ARTIFACT`: a second staging build of the same commit with version
`<version>.smoke` (job `build-staging-image`). Runs last because it replaces the
flashed CoreOS.

| Test | Description | Pass criteria |
|---|---|---|
| `test_core_api_updates` | Application (old → new), os-customization and CoreOS rootfs update via core API | Each update reports the expected current version; app and customization survive the CoreOS reboot |

---

## Not run in CI

| Test | Reason |
|---|---|
| `test_sdcard` | Marker `destructive` (overwrites the removable SD card), excluded from step 1 |
| Feature tests (`modem`, `wifi`, `serial`, `io4edge`, `ethernet2` on CPU01Plus) | Feature not declared in the CI station configuration |
