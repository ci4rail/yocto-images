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
from .ota import signing_key, signing_material, sign_artifact
from .test_application_module import application_images, write
from .test_application_mender_update import MenderApplication

pytestmark = pytest.mark.hardware


class APIApplication(MenderApplication):
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
        remote = f"{files}/app.mender"
        self.dut.put(output, remote)
        return remote


@pytest.fixture
def core_api_smoke(station, tmp_path):
    source = station.cfg.get("core_api_core_os_artifact")
    if not source:
        pytest.skip("Set core_api_core_os_artifact to a staging rebuild with a new image version")
    source = Path(source).expanduser().resolve()
    assert source.is_file(), f"CoreOS artifact not found: {source}"
    tool = shutil.which(station.cfg.get("mender_artifact_binary", "mender-artifact"))
    assert tool, "Install mender-artifact on the pytest runner"
    key = signing_key(station.cfg, "os-customization")
    api = CoreAPI(station)
    api.wait_ready()
    current = api.current("/software/core-os")
    expected = core_version(source)
    assert current["name"] == expected["name"], (current, expected)
    assert current["version"] != expected["version"], "CoreOS smoke rebuild must have a new /etc/issue version"
    # Re-sign a local copy; retain the supplied build artifact unchanged.
    signed = tmp_path / "core.mender"
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
