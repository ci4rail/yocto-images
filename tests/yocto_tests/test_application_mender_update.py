"""Install real app Artifacts through the target's standalone Mender client.

Reuse payload/container helpers, but drive all update states through the client.
Use the installed configuration, app store, state scripts and client database.
The installed app and docker-compose executables are used unchanged.
"""
import shutil
import subprocess

import pytest

from .station import quote
from .test_application_module import Application, application_images

pytestmark = pytest.mark.hardware


class MenderApplication(Application):
    def __init__(self, station, root, images, artifact_tool, local):
        super().__init__(station, root, images)
        self.artifact_tool, self.local = artifact_tool, local
        self.signing_key = station.cfg.get("mender_artifact_signing_key")
        self.pending = False
        values = self.dut.run("cat /var/lib/mender/device_type").splitlines()
        self.device_type = next(line.split("=", 1)[1] for line in values
                                if line.startswith("device_type="))
        assert self.device_type, "Target device_type is empty"

    def configure(self, health=False, timeout=8):
        # Read the installed module configuration; do not replace it for tests.
        settings = self.dut.run(
            "PERSISTENT_STORE=/data/mender-app; APP_HEALTHCHECK_ENABLED=yes\n"
            "if test -f /etc/mender/mender-app.conf; then "
            ". /etc/mender/mender-app.conf; fi\n"
            "printf '%s\\n' \"$PERSISTENT_STORE\" \"$APP_HEALTHCHECK_ENABLED\"").splitlines()
        self.store, enabled = settings
        self.current = f"{self.store}/{self.name}"
        if health and enabled != "yes":
            pytest.skip("Installed app configuration must enable APP_HEALTHCHECK_ENABLED")

    def client(self, *args, expected=0):
        command = " ".join(quote(arg) for arg in args)
        out = self.dut.run(
            f"mender-update {command} 2>&1\n"
            "rc=$?\nprintf '\\nCLIENT_EXIT=%s\\n' \"$rc\"", timeout=300)
        log, status = out.rsplit("CLIENT_EXIT=", 1)
        code = int(status.strip())
        if args[0] == "install" and code == 0:
            self.pending = True
        elif args[0] in ("commit", "rollback") and code in (0, 2):
            self.pending = False
        assert code == expected, f"mender-update {command}:\n{out}"
        return log.strip()

    def state(self, *args, **kwargs):
        raise AssertionError("These tests must use mender-update, not direct module states")

    def artifact(self, version="old", **kwargs):
        files = self.payload(version, **kwargs)
        directory = self.local / str(self.index)
        directory.mkdir()
        for filename in ("images.tar.gz", "manifests.tar.gz"):
            self.dut.get(f"{files}/files/{filename}", directory / filename)
        self.dut.get(f"{files}/header/meta-data", directory / "meta-data")
        name = f"{self.name}-{self.index}"
        output = directory / "app.mender"
        command = [
            self.artifact_tool, "write", "module-image", "-T", "app",
            "-t", self.device_type, "-n", name, "-o", str(output),
            "-m", str(directory / "meta-data"),
            "-f", str(directory / "images.tar.gz"),
            "-f", str(directory / "manifests.tar.gz"),
        ]
        if self.signing_key:
            command.extend(["-k", self.signing_key])
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        remote = f"{files}/app.mender"
        self.dut.put(output, remote)
        return remote, name

    def baseline(self, **kwargs):
        artifact, name = self.artifact(**kwargs)
        self.client("install", artifact)
        self.client("commit")
        assert self.client("show-artifact") == name
        self.assert_clean()
        return name

    def assert_clean(self):
        # Exit 2 is the client's documented 'no update in progress' result.
        self.client("commit", expected=2)
        self.dut.run(f"test ! -L {quote(self.store + '/.transactions/' + self.name + '.lock')}")

    def close(self):
        super().close()
        self.dut.run(f"rm -rf {quote(self.current)}")


@pytest.fixture
def mender_application(station, request, tmp_path):
    station.dut.run("command -v mender-update >/dev/null")
    tool = shutil.which(station.cfg.get("mender_artifact_binary", "mender-artifact"))
    assert tool, "Install mender-artifact on the pytest runner or set mender_artifact_binary"
    # Resolve the image fixture only after checking client prerequisites.
    root, images = request.getfixturevalue("application_images")
    app = MenderApplication(station, root, images, tool, tmp_path)
    try:
        yield app
    finally:
        # Only roll back an installation successfully started by this test.
        # Keep recovery files if rollback itself fails.
        if app.pending:
            app.client("rollback")
        app.close()


def test_install_and_commit(mender_application):
    app = mender_application
    app.baseline()
    artifact, name = app.artifact("new")
    app.client("install", artifact)
    app.assert_running("new")
    app.client("commit")
    assert app.client("show-artifact") == name
    app.assert_running("new")
    app.assert_clean()


@pytest.mark.parametrize("manifest_only", [False, True], ids=["reused-tag", "manifest-only"])
def test_explicit_rollback_and_next_update(mender_application, manifest_only):
    app = mender_application
    previous = app.baseline()
    artifact, _ = app.artifact("new", empty=manifest_only)
    app.client("install", artifact)
    app.assert_running("old" if manifest_only else "new")
    app.client("rollback")
    app.assert_running("old")
    assert app.dut.run(f"docker image inspect --format '{{{{.Id}}}}' {app.tag}").strip() == app.images["old"]
    assert app.client("show-artifact") == previous
    app.assert_clean()
    app.baseline(version="new")
    app.assert_running("new")


def test_first_install_rollback(mender_application):
    app = mender_application
    artifact, _ = app.artifact()
    app.client("install", artifact)
    app.assert_running("old")
    app.client("rollback")
    app.dut.run(f"test ! -e {app.current}")
    assert not app.dut.run(f"docker ps -aq --filter label=com.docker.compose.project={app.name}").strip()
    app.assert_clean()


@pytest.mark.parametrize("payload", [
    {"corrupt": True}, {"invalid": True}, {"command": ["/no-such-executable"]},
    {"health": "exit 1"},
], ids=["corrupt-image", "invalid-manifest", "start-failure", "unhealthy"])
def test_install_failure_automatically_rolls_back(mender_application, payload):
    app = mender_application
    if "health" in payload:
        app.configure(health=True)
    previous = app.baseline()
    artifact, _ = app.artifact("new", **payload)
    app.client("install", artifact, expected=1)
    # No explicit rollback: the client must run rollback and cleanup on failure.
    app.assert_running("old")
    assert app.client("show-artifact") == previous
    app.assert_clean()
    app.baseline(version="new")
    app.assert_running("new")


def test_commit_failure_automatically_rolls_back(mender_application):
    app = mender_application
    previous = app.baseline()
    app.configure(health=True)
    artifact, _ = app.artifact("new", health="test ! -f /tmp/unhealthy")
    app.client("install", artifact)
    cid = app.container()["Id"]
    app.dut.run(f"docker exec {cid} touch /tmp/unhealthy")
    app.dut.run("timeout 15 sh -c " + quote(
        f"while test \"$(docker inspect --format '{{{{.State.Health.Status}}}}' {cid})\" != unhealthy; do sleep 1; done"))
    app.client("commit", expected=1)
    app.assert_running("old")
    assert app.client("show-artifact") == previous
    app.assert_clean()
