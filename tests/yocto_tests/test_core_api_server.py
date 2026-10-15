"""Staging OTA smoke test through the installed core API (no direct client updates)."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time
import uuid

import paramiko
import pytest

from .station import quote
from .ota import signing_key, signing_material, sign_artifact
from .test_application_module import application_images, write
from .test_application_mender_update import MenderApplication

pytestmark = pytest.mark.hardware
STAGING_KEYS = Path(__file__).resolve().parents[2] / "ota-signing-material/staging"
if not STAGING_KEYS.is_dir():
    STAGING_KEYS = Path(__file__).resolve().parents[1] / "staging-ota-keys"


def artifact_command(command):
    result = subprocess.run([str(arg) for arg in command], capture_output=True,
                            text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr


def staging_signing_key(keys, directory):
    # Older mender-artifact releases cannot read the repository's PKCS#8 RSA
    # key. Convert its encoding locally; the key and signature identity stay the same.
    key = directory / "staging-signing.pem"
    key.touch(mode=0o600)
    artifact_command(["openssl", "pkey", "-traditional", "-in",
                      keys / "ci4rail-artifact.key.pem", "-out", key])
    return key


def core_version(artifact):
    """Read expected version from an Artifact; installed versions come only from HTTP."""
    with tarfile.open(artifact) as outer:
        header = next(member for member in outer
                      if member.name in ("header.tar.gz", "header.tar"))
        with tarfile.open(fileobj=outer.extractfile(header), mode="r|*") as inner:
            for member in inner:
                if member.name == "headers/0000/type-info":
                    info = json.load(inner.extractfile(member))
                    assert info["type"] == "bootfit-rootfs", "Use a paired staging FIT/rootfs artifact"
                    provides = info["artifact_provides"]
                    value = provides.get("rootfs-image.version") or provides["rootfs-image.bootfit-rootfs.version"]
                    # Current builds use cpu01-standard-vX; legacy image names are
                    # accepted by the server too. Split at the version boundary.
                    match = re.fullmatch(r"(.+?)(?:-image)?(?:_|-)(v\d+\.\d+\.\d+.*)", value)
                    assert match, f"Unrecognized CoreOS version: {value}"
                    return {"name": match[1].lower().replace("_", "-"), "version": match[2]}
    raise AssertionError("Artifact has no type-info")


class CoreAPI:
    def __init__(self, station):
        self.dut = station.dut
        self.url = station.cfg.get("core_api_url", "http://127.0.0.1:8090/api/v1").rstrip("/")
        self.timeout = station.cfg.get("core_api_update_timeout", 900)
        self.completed = False

    def request(self, endpoint, artifact=None):
        # Run curl on the DUT so the API need not be exposed to the test runner.
        command = f"curl --silent --show-error --max-time {180 if artifact else 10} --write-out '\\n%{{http_code}}' "
        if artifact is not None:
            command += ("--request PUT --header 'Content-Type: application/octet-stream' "
                        f"--data-binary {quote('@' + artifact)} ")
        output = self.dut.run(command + quote(self.url + endpoint), timeout=200)
        body, status = output.rsplit("\n", 1)
        try:
            response = json.loads(body) if body.strip() else None
        except json.JSONDecodeError:
            response = body
        return int(status), response

    def current(self, endpoint):
        status, response = self.request(endpoint)
        assert status == 200, response
        return response["current"]

    def wait_ready(self, timeout=120):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                status, response = self.request("/software/core-os")
            except (OSError, paramiko.SSHException, RuntimeError, TimeoutError) as exc:
                last = str(exc)
            else:
                if status == 200:
                    return
                last = (status, response)
                assert status in (502, 503), f"Core API readiness: HTTP {status}: {response}"
            time.sleep(3)
        pytest.fail(f"Core API did not become ready within {timeout}s: {last}")

    def deploy(self, endpoint, artifact, expected):
        print(f"Core API: deploying {endpoint}: {expected}", flush=True)
        status, response = self.request(endpoint, artifact)
        assert status == 202, f"Upload rejected: HTTP {status}: {response}"
        deadline = time.monotonic() + self.timeout
        last = response
        while time.monotonic() < deadline:
            try:
                status, response = self.request(endpoint)
            except (OSError, paramiko.SSHException, RuntimeError, TimeoutError) as exc:
                # CoreOS and customization updates reboot the DUT and API.
                last = str(exc)
            else:
                last = response
                if status == 200:
                    code = response["deploy_status"]["code"]
                    assert code != "failure", response
                    if code == "success" and response["current"] == expected:
                        # Query again after completion: verify the installed version,
                        # not the version queued in the upload response.
                        assert self.current(endpoint) == expected
                        return
                    assert code in ("waiting", "in_progress", "success", "never_deployed"), response
                else:
                    assert status in (502, 503), f"HTTP {status}: {response}"
            time.sleep(3)
        pytest.fail(f"Timed out waiting for {endpoint} to install {expected}: {last}")


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
        payload = tmp_path / "customization"
        payload.mkdir()
        (payload / "etc").mkdir()
        (payload / "manifest.json").write_text(json.dumps({
            "format_version": 1, "version": version,
            "health_checks": [{"type": "command", "command": ["true"]}],
        }))
        archive = tmp_path / "customization.tar"
        with tarfile.open(archive, "w") as tar:
            tar.add(payload / "manifest.json", arcname="manifest.json")
            tar.add(payload / "etc", arcname="etc")
        artifact = tmp_path / "customization.mender"
        artifact_command([tool, "write", "module-image", "-T", "os-customization",
                          "-n", version, "-t", app.device_type, "-f", archive,
                          "-o", artifact])
        sign_artifact(tool, artifact, station.cfg, "os-customization")
        station.dut.put(artifact, remote + "/customization.mender")
        api.deploy("/software/core-os-customization", remote + "/customization.mender",
                   {"name": "core-os-customization", "version": version})
        api.deploy("/software/core-os", remote + "/core.mender", expected)
        # The committed non-rootfs updates must survive the CoreOS reboot too.
        assert api.current(f"/software/application/{app.name}") == {"name": app.name, "version": "new"}
        assert api.current("/software/core-os-customization") == {
            "name": "core-os-customization", "version": version}
        api.completed = True
    finally:
        if api.completed:
            app.close()
