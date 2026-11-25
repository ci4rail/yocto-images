"""io4edge firmware update through the core API.

Both CI DUTs carry the CPU01 microcontroller (io4edge device S101-CPU01UC on the
internal link usb_io_ctrl). The test swaps its firmware between two releases of
ci4rail/fw_esp_cpu01uc_default and back, through `PUT /software/io4edge/{device}`.
The core API finds the device with io4edge-cli via avahi (mDNS) and loads the
firmware over TCP, so the test also proves that the installed profile leaves the
io4edge links open.

The firmware repository is private and this repository is public: the packages
are neither committed nor bundled. They are downloaded on the pytest runner with
the GitHub CLI (authenticated on the TC) or taken from `io4edge_firmware_dir`.
"""
import hashlib
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from .core_api import CoreAPI
from .station import quote

pytestmark = [pytest.mark.hardware, pytest.mark.profile]

FIRMWARE_REPO = "ci4rail/fw_esp_cpu01uc_default"
# Release tag -> SHA-256 of its fw-cpu01uc-default-<tag>.fwpkg.
FIRMWARE = {
    "1.1.0": "a40b92826b7793fb2ad17701f941cb3e6b62e7fd77a8f601501229c803c34a42",
    "1.1.1-rc.1": "90aa0369bd4373181bc398d8b0d9ddbfe750f9ab5e81812034520035e7029f70",
}
# The device reports the manifest name with the "fw-" prefix.
FIRMWARE_NAME = "fw-cpu01uc-default"


def package_name(version):
    return f"{FIRMWARE_NAME}-{version}.fwpkg"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def firmware_package(station, version, directory):
    """Local path of the firmware package of `version`, checked against its pinned hash."""
    cached = station.cfg.get("io4edge_firmware_dir")
    path = Path(cached).expanduser() / package_name(version) if cached else None
    if path is None or not path.is_file():
        gh = shutil.which("gh")
        assert gh, (f"Install and authenticate the GitHub CLI on the pytest runner, or put "
                    f"{package_name(version)} into io4edge_firmware_dir")
        result = subprocess.run([gh, "release", "download", version, "-R", FIRMWARE_REPO,
                                 "-p", package_name(version), "-D", str(directory)],
                                capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, f"Download of {package_name(version)} failed:\n{result.stderr}"
        path = Path(directory) / package_name(version)
    assert sha256(path) == FIRMWARE[version], f"{path} does not match the pinned SHA-256"
    return path


def versions_to_deploy(current):
    """Swap to the other release and back. A device with another firmware ends on 1.1.0."""
    first, second = FIRMWARE
    if current == first:
        return [second, first]
    if current == second:
        return [first, second]
    return [second, first]


@pytest.fixture
def io4edge_device(station):
    device = station.cfg.get("io4edge_update_device")
    if not device:
        pytest.skip("Set io4edge_update_device in the station configuration")
    return device


def test_io4edge_update(station, io4edge_device, tmp_path):
    api = CoreAPI(station)
    api.wait_ready()
    # The device list comes from io4edge-cli scan (avahi), the state from io4edge-cli fw (TCP).
    status, devices = api.request("/hardware/io4edge-devices")
    assert status == 200 and io4edge_device in (devices or []), \
        f"Core API does not see {io4edge_device}: HTTP {status}: {devices}"
    endpoint = f"/software/io4edge/{io4edge_device}"
    current = api.current(endpoint)
    assert current["name"] == FIRMWARE_NAME, current
    versions = versions_to_deploy(current["version"])
    packages = {version: firmware_package(station, version, tmp_path) for version in versions}
    remote = "/data/io4edge-update-" + uuid.uuid4().hex
    station.dut.run(f"mkdir -p {remote}")
    try:
        for version in versions:
            target = f"{remote}/{package_name(version)}"
            station.dut.put(packages[version], target)
            api.deploy(endpoint, target, {"name": FIRMWARE_NAME, "version": version})
        # Deploying the installed version again is refused.
        status, response = api.request(endpoint, f"{remote}/{package_name(versions[-1])}")
        assert status == 409, f"Redeploy of the installed firmware: HTTP {status}: {response}"
    finally:
        station.dut.run(f"rm -rf {quote(remote)}", check=False)
