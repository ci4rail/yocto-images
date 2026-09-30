Folder for integration tests on target machine

All tests shall use pytest.

Ultimately, tests execute against a target closed with staging keys. Initially the target is open; see the agreed implementation details below.

# Test environment

The target is connected to a test computer (TC) that is in the same network as the target and the development computer or ci runner.

The TC is a raspberry pi that has these connections to the target device:
- USB /dev/ttyUSBx to target console
- USB acting as a USB gadget - mass storage, used to provide the image file for flashing the target device
- USB acting as a master - to flash the device in recovery mode
- Tinkerforge Industrial Dual Relay Bricklet - Relay 0 for power control, Relay 1 for recovery mode

# Who executes the tests

Test shall be executable from the development computer and the test computer (TC).

# Device flashing

The test setup shall initially flash the device with a given image file in tezi.tar format, thus erasing everything on the emmc of the target device (except fuses).
To flash the device, first convert tezi file into disk image via scripts/make-gadget-image.sh, then transfer image to TC and
execute tdx-installer container, see https://raw.githubusercontent.com/ci4rail/tc-lib/c16e1d0c68bdd802066f9f09f58aebd12901bd9c/dut_ctrl/tdx_inst.resource.

# Tests

Start with porting existing robot framework tests to pytest: https://github.com/ci4rail/cpu01-yocto-testcases/tree/yoto-security-tests.

# Test setuo configuration

Make the following parameters configurable for the test setup:
- TARGET_IP: IP address of the target device
- TC_IP: IP address of the test computer
- IMAGE_FILE: Path to the image file to be flashed
- SERIAL_PORT: Serial port of the target console on the test computer
- IP/PORT of Tinkerforge brickd server on the test computer
- Tinkerforge UID of the connected relay bricklet on the test computer
- SIM APN
- Wifi SSID and password to connect to for testing
- SSH credentials for the target device (username and password or key file)

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
  authentication expectations initially. Keep station credentials outside
  committed files and CI artifacts.

- After successful flashing, power cycle the target before waiting for SSH;
  the installer's warm reboot is insufficient.
