Pytest suite for ModuCop target integration tests and local harness unit tests.

All tests shall use pytest.

Hardware tests currently execute against open CPU01 and CPU01Plus standard-image targets. Closed devices require a suitable signed installer; the default development installer is rejected for them.

# Test environment

The target is connected to a test computer (TC) that is in the same network as the target and the development computer or ci runner.

The TC is a raspberry pi that has these connections to the target device:
- USB /dev/ttyUSBx to target console
- USB acting as a USB gadget - mass storage, used to provide the image file for flashing the target device
- USB acting as a master - to flash the device in recovery mode
- Tinkerforge Industrial Dual Relay Bricklet - Relay 0 for power control, Relay 1 for recovery mode

# Who executes the tests

Test shall be executable from the development computer (DC) and the test computer (TC).

# Device flashing

Unless `--skip-flash` is set, session setup converts the supplied TEZI tarball into a disk image with `scripts/make-gadget-image.sh`, transfers it to the TC, and runs the configured tdx-installer container. This erases target eMMC except fuses. After flashing, setup releases recovery, power cycles the target, and waits for SSH. A station lock prevents concurrent use.

# Tests

Hardware tests in `yocto_tests/test_functional.py` cover SSH password authentication; Docker login, BusyBox and Compose networking; gpsd access from a container and a 3D GNSS fix; ETH1 and ETH2 throughput; LTE connectivity; eMMC and removable SD card throughput; Wi-Fi throughput; serial loopback; and io4edge discovery and reachability. Tests for optional hardware use the station's `features` list and skip when the feature is absent. The SD card test is marked `destructive` and overwrites the configured removable card.

`yocto_tests/test_persistence.py` checks that a journal entry survives a target reboot, that journal rotation creates a new file, that `/var/log` uses at most 100 MB after rotation and reboot, and that the root filesystem has at least 10% available capacity. It also sends over 100 MB of random log input, verifies that the journal accepted each chunk, and checks that persistent journal data stays within 100 MB after rotation. The logging test reboots the shared target, so run hardware tests sequentially.

Hardware security tests in `yocto_tests/test_security.py` cover TCP and UDP attack surface against the baseline, Nmap vulnerability-script output, privileged network services, nftables egress counters, and malformed TCP/UDP traffic followed by an availability check. The checks use `config/security_baseline.yaml`; the Nmap test checks vulnerability strings rather than CVSS scores.

`unit/test_harness.py` covers configuration and closed-device guards, shell quoting and sensitive output, station lock cleanup, network inventory parsing, nftables counters, package allowlisting, installer machine selection and timeout cleanup, power-cycle relay behavior, and reflashed SSH host-key policy. Unit tests do not require target hardware.

# Test setup configuration

Copy `config/station.example.yaml` to a local station configuration. It defines the platform, target and TC connections, image path, serial port, relay/brickd settings, optional hardware features, network and storage thresholds, SIM APN, Wi-Fi credentials, Docker credentials, and SSH credentials. Environment references in YAML are expanded on the pytest runner. Keep credentials outside committed files and CI artifacts.

Run through `run.sh` or a Python virtual environment. Hardware runs need `--station` and either `--image` or `image_file` in station configuration. Use `--skip-flash` to reuse an installed target. Run harness unit tests with `.venv/bin/python -m pytest unit`; `pytest.ini` selects hardware tests by default. Hardware tests share one target and must not run with pytest-xdist. See `README.md` for full setup and run instructions.

# Agreed implementation details

- Always run pytest in a Python virtual environment.
- Cover CPU01 and CPU01Plus standard images only.
- Devices are currently open. Start with ci4rail/tdx-installer-dev:v3;
  replace it with a suitable signed installer before testing closed devices.
- On the development computer, use the current user's SSH configuration/key
  to access the TC. On the TC, run commands locally.
- CI publishes a pytest package as a GitHub artifact. Hardware tests execute
  on the TC after download, never on the CI runner.
- TC system utilities are managed separately through Ansible. The TC user
  has Docker access without sudo and noninteractive sudo access.
- Keep the existing security baseline, performance thresholds and password
  authentication expectations.

- After successful flashing, power cycle the target before waiting for SSH;
  the installer's warm reboot is insufficient.
