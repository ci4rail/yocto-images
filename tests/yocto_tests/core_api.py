"""Core API client and artifact helpers shared by update and profile tests."""
import json
from pathlib import Path
import re
import subprocess
import tarfile
import time

import paramiko
import pytest

from .station import quote

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
