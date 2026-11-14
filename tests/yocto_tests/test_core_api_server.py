"""Staging OTA smoke test through the installed core API (no direct client updates)."""
from pathlib import Path
import shutil
import time  # noqa: F401  (unit tests patch time.sleep through this module)
import uuid

import pytest

from .station import quote
from .core_api import (STAGING_KEYS, CoreAPI, artifact_command,
                       core_version, staging_signing_key)
from .customization import ENDPOINT, NAME, build_customization
from .fit import TAMPER_MODES, repack_core_artifact, tampered_core_artifact
from .ota import (BAD_SIGNATURES, assert_signature_invalid, break_signature,
                  signing_key, signing_material, sign_artifact)
from .test_application_module import application_images, write
from .test_application_mender_update import MenderApplication

pytestmark = pytest.mark.hardware


class APIApplication(MenderApplication):
    # Set to one of BAD_SIGNATURES to build artifacts the target must reject.
    bad_signature = None

    def artifact(self, version="old", **kwargs):
        files = self.payload(version, **kwargs)
        write(self.dut, f"{files}/pack/manifests/.env", f"SOFTWARE_VERSION={version}\n")
        self.dut.run(f"tar -czf {files}/files/manifests.tar.gz -C {files}/pack manifests")
        directory = self.local / str(self.index)
        directory.mkdir()
        payloads = []
        for filename in ("images.tar.gz", "manifests.tar.gz", "meta-data"):
            source = f"{files}/header/{filename}" if filename == "meta-data" else f"{files}/files/{filename}"
            destination = directory / filename
            self.dut.get(source, destination)
            payloads.append(destination)
        output = directory / "app.mender"
        artifact_command([
            self.artifact_tool, "write", "module-image", "-T", "app",
            "-t", self.device_type, "-n", f"{self.name}-{version}", "-o", output,
            "-m", payloads[2], "-f", payloads[0], "-f", payloads[1],
            "--software-filesystem", "data-partition", "--software-name", self.name,
            "--software-version", version,
        ])
        sign_artifact(self.artifact_tool, output, self.station_config, "application")
        if self.bad_signature:
            break_signature(self.artifact_tool, output, self.bad_signature)
            assert_signature_invalid(self.artifact_tool, output, self.station_config)
        remote = f"{files}/app.mender"
        self.dut.put(output, remote)
        return remote


def core_os_source(station):
    source = station.cfg.get("core_api_core_os_artifact")
    if not source:
        pytest.skip("Set core_api_core_os_artifact to a staging rebuild with a new image version")
    source = Path(source).expanduser().resolve()
    assert source.is_file(), f"CoreOS artifact not found: {source}"
    tool = shutil.which(station.cfg.get("mender_artifact_binary", "mender-artifact"))
    assert tool, "Install mender-artifact on the pytest runner"
    return source, tool


@pytest.fixture
def core_os_work(station):
    """Source artifact, tool and a scratch directory for CoreOS-sized files.

    Repacking unpacks the ~1.4 GB rootfs. pytest's tmp_path is on the TC's small
    /tmp and keeps files until the session ends, so work next to the source
    artifact (or in core_os_work_dir) and delete everything after each test.
    """
    source, tool = core_os_source(station)
    base = Path(station.cfg.get("core_os_work_dir") or source.parent).expanduser()
    work = base / ("yocto-coreos-" + uuid.uuid4().hex)
    work.mkdir(parents=True)
    try:
        yield source, tool, work
    finally:
        shutil.rmtree(work, ignore_errors=True)


def boot_slot(dut):
    return dut.run("fw_printenv mender_boot_part upgrade_available").split()


def boot_id(dut):
    return dut.run("cat /proc/sys/kernel/random/boot_id").strip()


def device_type(dut):
    values = dut.run("cat /var/lib/mender/device_type").splitlines()
    return next(line.split("=", 1)[1] for line in values if line.startswith("device_type="))


def assert_rejected_without_reboot(station, api, endpoint, remote, rejected):
    """The Mender client must refuse the artifact before installing anything."""
    boot = boot_id(station.dut)
    slot = boot_slot(station.dut)
    api.deploy_rejected(endpoint, remote, rejected, upload_rejection=True)
    assert boot_id(station.dut) == boot, "Target rebooted for an artifact with an invalid OTA signature"
    assert boot_slot(station.dut) == slot


@pytest.fixture
def remote_dir(station):
    remote = "/data/core-api-reject-" + uuid.uuid4().hex
    station.dut.run(f"mkdir -p {remote}")
    try:
        yield remote
    finally:
        station.dut.run(f"rm -rf {quote(remote)}", check=False)


@pytest.mark.parametrize("mode", BAD_SIGNATURES)
def test_core_os_rejects_bad_ota_signature(station, core_os_work, remote_dir, mode):
    """CoreOS artifact unsigned or signed with an unknown key: no install, no reboot."""
    source, tool, work = core_os_work
    artifact = repack_core_artifact(tool, source, work, station.cfg, f"ota-{mode}")
    break_signature(tool, artifact, mode)
    assert_signature_invalid(tool, artifact, station.cfg)
    api = CoreAPI(station)
    api.wait_ready()
    station.dut.put(artifact, remote_dir + "/core.mender")
    assert_rejected_without_reboot(station, api, "/software/core-os", remote_dir + "/core.mender",
                                   core_version(artifact))


@pytest.mark.parametrize("mode", BAD_SIGNATURES)
def test_customization_rejects_bad_ota_signature(station, tmp_path, remote_dir, mode):
    """os-customization artifact unsigned or signed with an unknown key: no install, no reboot."""
    tool = shutil.which(station.cfg.get("mender_artifact_binary", "mender-artifact"))
    assert tool, "Install mender-artifact on the pytest runner"
    version = f"ota-{mode}-" + uuid.uuid4().hex[:8]
    artifact = build_customization(tool, version, device_type(station.dut), tmp_path, station.cfg)
    break_signature(tool, artifact, mode)
    assert_signature_invalid(tool, artifact, station.cfg)
    api = CoreAPI(station)
    api.wait_ready()
    station.dut.put(artifact, remote_dir + "/customization.mender")
    assert_rejected_without_reboot(station, api, ENDPOINT, remote_dir + "/customization.mender",
                                   {"name": NAME, "version": version})


@pytest.mark.parametrize("mode", BAD_SIGNATURES)
def test_application_rejects_bad_ota_signature(station, request, tmp_path, mode):
    """Application artifact unsigned or signed with an unknown key: not installed."""
    tool = shutil.which(station.cfg.get("mender_artifact_binary", "mender-artifact"))
    assert tool, "Install mender-artifact on the pytest runner"
    api = CoreAPI(station)
    api.wait_ready()
    root, images = request.getfixturevalue("application_images")
    app = APIApplication(station, root, images, tool, tmp_path)
    app.bad_signature = mode
    try:
        artifact = app.artifact("old")
        assert_rejected_without_reboot(station, api, f"/software/application/{app.name}", artifact,
                                       {"name": app.name, "version": "old"})
        status, applications = api.request("/software/applications")
        # The API encodes an empty application list as null.
        assert status == 200 and app.name not in (applications or []), applications
    finally:
        app.close()


@pytest.mark.parametrize("mode", TAMPER_MODES)
def test_core_os_rejects_manipulated_fit(station, core_os_work, remote_dir, mode):
    """A CoreOS artifact with a valid OTA signature but an unsigned or wrongly
    signed boot FIT must not become active: U-Boot refuses the FIT and Mender
    rolls back to the running slot."""
    source, tool, work = core_os_work
    artifact = tampered_core_artifact(tool, source, mode, work, station.cfg)
    keys = signing_material(station.cfg)
    if keys:
        artifact_command([tool, "validate", "-k", keys / "ci4rail-artifact-pub.pem", artifact])
    rejected = core_version(artifact)
    api = CoreAPI(station)
    api.wait_ready()
    slot = boot_slot(station.dut)
    assert slot[1] == "upgrade_available=0", slot
    station.dut.put(artifact, remote_dir + "/core.mender")
    api.deploy_rejected("/software/core-os", remote_dir + "/core.mender", rejected)
    assert boot_slot(station.dut) == slot, "Target did not return to the original boot slot"
    station.dut.run("findmnt -no SOURCE / | grep -q '^/dev/\\(dm-\\|mapper/\\)'")


@pytest.fixture
def core_api_smoke(station, core_os_work):
    source, tool, work = core_os_work
    key = signing_key(station.cfg, "os-customization")
    api = CoreAPI(station)
    api.wait_ready()
    current = api.current("/software/core-os")
    expected = core_version(source)
    assert current["name"] == expected["name"], (current, expected)
    assert current["version"] != expected["version"], "CoreOS smoke rebuild must have a new /etc/issue version"
    # Re-sign a local copy; retain the supplied build artifact unchanged.
    signed = work / "core.mender"
    shutil.copyfile(source, signed)
    sign_artifact(tool, signed, station.cfg, "core-os")
    keys = signing_material(station.cfg)
    if keys:
        artifact_command([tool, "validate", "-k", keys / "ci4rail-artifact-pub.pem", signed])
    remote = "/data/core-api-smoke-" + uuid.uuid4().hex
    station.dut.run(f"mkdir -p {remote}")
    try:
        station.dut.put(signed, remote + "/core.mender")
        yield api, tool, key, remote, expected
    finally:
        # A failed asynchronous job might still need its upload; preserve files
        # on failure rather than deleting an in-flight update or forcing rollback.
        if api.completed:
            station.dut.run(f"rm -rf {quote(remote)}")


def test_core_api_updates(station, core_api_smoke, request, tmp_path):
    api, tool, key, remote, expected = core_api_smoke
    # Build images only after preflight has validated the staging CoreOS artifact.
    root, images = request.getfixturevalue("application_images")
    app = APIApplication(station, root, images, tool, tmp_path)
    app.signing_key = key
    try:
        for version in ("old", "new"):
            artifact = app.artifact(version)
            api.deploy(f"/software/application/{app.name}", artifact,
                       {"name": app.name, "version": version})
            app.assert_running(version)
        status, applications = api.request("/software/applications")
        assert status == 200 and app.name in applications, applications
        version = "smoke-" + uuid.uuid4().hex
        artifact = build_customization(tool, version, app.device_type, tmp_path, station.cfg)
        station.dut.put(artifact, remote + "/customization.mender")
        api.deploy(ENDPOINT, remote + "/customization.mender", {"name": NAME, "version": version})
        api.deploy("/software/core-os", remote + "/core.mender", expected)
        # The committed non-rootfs updates must survive the CoreOS reboot too.
        assert api.current(f"/software/application/{app.name}") == {"name": app.name, "version": "new"}
        assert api.current(ENDPOINT) == {"name": NAME, "version": version}
        api.completed = True
    finally:
        if api.completed:
            app.close()
