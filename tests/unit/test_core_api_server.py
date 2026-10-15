"""Exercise API completion checks and real signed artifact compatibility locally."""
import shutil
from types import SimpleNamespace

import pytest

from yocto_tests.test_core_api_server import CoreAPI, core_version, artifact_command, STAGING_KEYS, staging_signing_key


def test_deploy_requires_new_installed_version(monkeypatch):
    monkeypatch.setattr("yocto_tests.test_core_api_server.time.sleep", lambda _: None)
    expected = {"name": "cpu01-standard", "version": "v3.0.0.smoke"}
    old = {**expected, "version": "v3.0.0"}
    replies = iter([
        (202, None), RuntimeError("reboot disconnected SSH"),
        (200, {"current": old, "deploy_status": {"code": "success"}}),
        (200, {"current": expected, "deploy_status": {"code": "success"}}),
        (200, {"current": expected}),
    ])
    api = CoreAPI(SimpleNamespace(dut=None, cfg={}))
    calls = []

    def request(*args):
        calls.append(args)
        response = next(replies)
        if isinstance(response, Exception):
            raise response
        return response

    api.request = request
    api.deploy("/software/core-os", "/data/update.mender", expected)
    assert len(calls) == 5


def test_deploy_failure_is_not_treated_as_reboot():
    api = CoreAPI(SimpleNamespace(dut=None, cfg={}))
    replies = iter([(202, None), (200, {"deploy_status": {
        "code": "failure", "message": "signature rejected"}})])
    api.request = lambda *args: next(replies)
    with pytest.raises(AssertionError, match="signature rejected"):
        api.deploy("/software/core-os", "/data/update.mender", {})


def test_deploy_times_out(monkeypatch):
    api = CoreAPI(SimpleNamespace(dut=None, cfg={"core_api_update_timeout": 1}))
    api.request = lambda *args: (202, None)
    times = iter([0, 2])
    monkeypatch.setattr("yocto_tests.test_core_api_server.time.monotonic", lambda: next(times))
    with pytest.raises(pytest.fail.Exception, match="Timed out"):
        api.deploy("/software/core-os", "/data/update.mender", {})


def test_request_quotes_remote_artifact_and_parses_status():
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return '{"code":"queued"}\n202'

    api = CoreAPI(SimpleNamespace(dut=SimpleNamespace(run=run), cfg={}))
    assert api.request("/software/core-os", "/data/test artifact.mender") == (202, {"code": "queued"})
    assert "--data-binary '@/data/test artifact.mender'" in calls[0]
    assert "%{http_code}" in calls[0]
    assert "mender-update" not in calls[0]


@pytest.mark.parametrize("value", [
    "cpu01-standard-v3.0.0.smoke.1",
    "cpu01-standard-image-v3.0.0.smoke.1",
])
def test_core_version_matches_signed_artifact(tmp_path, value):
    tool = shutil.which("mender-artifact")
    if not tool:
        pytest.skip("mender-artifact not installed")
    payload = tmp_path / "boot.itb"
    payload.write_bytes(b"test FIT payload")
    artifact = tmp_path / "core.mender"
    key = staging_signing_key(STAGING_KEYS, tmp_path)
    artifact_command([tool, "write", "module-image", "-T", "bootfit-rootfs",
                      "-n", value, "-t", "moducop-cpu01", "-f", payload,
                      "-o", artifact, "-k", key])
    artifact_command([tool, "validate", "-k", STAGING_KEYS / "ci4rail-artifact-pub.pem", artifact])
    assert core_version(artifact) == {"name": "cpu01-standard", "version": "v3.0.0.smoke.1"}


def test_readiness_waits_for_api_after_ssh(monkeypatch):
    monkeypatch.setattr("yocto_tests.test_core_api_server.time.sleep", lambda _: None)
    api = CoreAPI(SimpleNamespace(dut=None, cfg={}))
    replies = iter([RuntimeError("connection refused"), (503, None), (200, {"current": {}})])

    def request(endpoint):
        assert endpoint == "/software/core-os"
        result = next(replies)
        if isinstance(result, Exception):
            raise result
        return result

    api.request = request
    api.wait_ready()


def test_readiness_times_out(monkeypatch):
    api = CoreAPI(SimpleNamespace(dut=None, cfg={}))
    times = iter([0, 121])
    monkeypatch.setattr("yocto_tests.test_core_api_server.time.monotonic", lambda: next(times))
    with pytest.raises(pytest.fail.Exception, match="did not become ready"):
        api.wait_ready()
