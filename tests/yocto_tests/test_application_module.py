"""Exercise the installed Mender app module against real containers.

Invoke the Update Module v3 states directly with real payload trees. This keeps
Mender's server connection, inventory and deployment database untouched. Faults
are injected at helper-call boundaries; no target reboot is required.
"""
import json
import uuid

import pytest
import yaml

from .station import quote

pytestmark = pytest.mark.hardware
APP = "/usr/share/mender/modules/v3/app"
COMPOSE = "/usr/share/mender/app-modules/v1/docker-compose"


def write(dut, path, content):
    dut.run(f"cat > {quote(path)}", input=content)


def write_many(dut, files):
    dut.run("set -e\n" + "\n".join(
        f"printf %s {quote(content)} > {quote(path)}" for path, content in files.items()))


@pytest.fixture(scope="module")
def application_images(station):
    dut = station.dut
    namespace = "yocto-app-" + uuid.uuid4().hex[:12]
    root = f"/data/{namespace}"
    base = station.cfg.get("application_test_image", "alpine:latest")
    dut.run(f"test -x {APP} && test -x {COMPOSE}")
    assert dut.run(f"{APP} SupportsRollback").strip() == "Yes", "Install the rollback-capable app module first"
    dut.run(f"docker image inspect {quote(base)} >/dev/null 2>&1 || docker pull {quote(base)}", timeout=180)
    dut.run(f"mkdir -p {root}/build")
    images = {}
    try:
        for version in ("old", "new"):
            tag = f"{namespace}:{version}"
            write(dut, f"{root}/build/Dockerfile",
                  f"FROM {base}\nLABEL io.ci4rail.yocto-test={namespace} version={version}\n")
            dut.run(f"docker build --network=none -t {tag} {root}/build", timeout=180)
            images[version] = dut.run(f"docker image inspect --format '{{{{.Id}}}}' {tag}").strip()
        yield root, images
    finally:
        for version in ("old", "new"):
            dut.run(f"docker image rm {namespace}:{version}", check=False)
        dut.run(f"rm -rf {root}")


class Application:
    def __init__(self, station, root, images):
        self.dut, self.images = station.dut, images
        self.name = "app" + uuid.uuid4().hex[:12]
        self.root = f"{root}/{self.name}"
        self.store = f"{self.root}/store"
        self.current = f"{self.store}/{self.name}"
        self.tag = f"{self.name}:live"
        self.extra_tags = []
        self.config = f"{self.root}/app.conf"
        self.index = 0
        self.fault_pending = False
        self.dut.run(f"mkdir -p {self.root}/helpers {self.store}")
        self.configure()
        # Wrapper normally execs the installed helper unchanged. Fault injection
        # is confined to this test's configuration and one-use marker file.
        write(self.dut, f"{self.root}/helpers/docker-compose", f'''#!/bin/sh
if test -f {self.root}/fault; then
    read mode state < {self.root}/fault
    if test "$1" = "$state"; then
        rm -f {self.root}/fault
        case "$mode" in
            fail) echo "Injected $state failure" >&2; exit 42 ;;
            kill-before) kill -KILL "$PPID"; exit 137 ;;
            kill-after) {COMPOSE} "$@" || exit $?; kill -KILL "$PPID"; exit 137 ;;
        esac
    fi
fi
# Docker 25 exports include wall-clock tar headers. Ordinary delta fixtures
# freeze the export, but still require SAVE to resolve the expected live image.
if test "$1" = SAVE && test -f {self.root}/delta-base.tar; then
    actual=$(docker image inspect --format '{{{{.Id}}}}' "$3") || exit $?
    test "$actual" = "$(cat {self.root}/delta-base.id)" || exit 43
    cp {self.root}/delta-base.tar "$4"
    exit $?
fi
exec {COMPOSE} "$@"
''')
        self.dut.run(f"chmod 755 {self.root}/helpers/docker-compose")

    def configure(self, health=False, timeout=8):
        write(self.dut, self.config,
              f"PERSISTENT_STORE={quote(self.store)}\n"
              f"APP_MODULE_DIR={quote(self.root + '/helpers')}\n"
              f"APP_HEALTHCHECK_ENABLED={'yes' if health else 'no'}\n"
              f"APP_HEALTHCHECK_TIMEOUT={timeout}\nAPP_HEALTHCHECK_INTERVAL=1\n")

    def fault(self, state, mode="fail"):
        self.fault_pending = True
        write(self.dut, f"{self.root}/fault", f"{mode} {state}\n")

    def payload(self, version="old", health=None, command=None, oneshot=False,
                corrupt=False, empty=False, invalid=False):
        self.index += 1
        files = f"{self.root}/payload{self.index}"
        self.dut.run(f"mkdir -p {files}/header {files}/files {files}/tmp "
                     f"{files}/pack/images/image {files}/pack/manifests")
        metadata = dict(application_name=self.name, orchestrator="docker-compose",
                        version="1.0", platform="linux/arm64")
        contents = {
            f"{files}/header/meta-data": json.dumps(metadata),
            f"{files}/header/header-info": json.dumps(
                {"artifact_provides": {"artifact_name": f"{self.name}-{self.index}"}}),
        }
        service = dict(image=self.tag, command=command or ["sh", "-c", "while :; do sleep 1; done"],
                       network_mode="none", stop_grace_period="1s")
        if health is not None:
            service["healthcheck"] = dict(test=["CMD-SHELL", health], interval="1s", timeout="1s", retries=1)
        if oneshot:
            service["labels"] = {"io.ci4rail.mender.oneshot": "true"}
        composition = dict(version="3.7", services={"worker": service})
        contents[f"{files}/pack/manifests/docker-compose.yml"] = (
            "services: [invalid]\n" if invalid else yaml.safe_dump(composition))
        if empty:
            self.dut.run(f"rmdir {files}/pack/images/image")
        elif corrupt:
            contents[f"{files}/pack/images/image/image.img"] = "not a container archive\n"
        else:
            # Prepare an archive with a reused tag, then restore its original
            # mapping before installation so the module must do the actual move.
            self.dut.run(f'''set -e
old=$(docker image inspect --format '{{{{.Id}}}}' {self.tag} 2>/dev/null || true)
trap 'if test -n "$old"; then docker tag "$old" {self.tag}; else docker image rm {self.tag} >/dev/null; fi' EXIT
docker tag {self.images[version]} {self.tag}
docker image save -o {files}/pack/images/image/image.img {self.tag}
''', timeout=120)
        if not empty:
            for filename in ("url-new.txt", "url-current.txt"):
                contents[f"{files}/pack/images/image/{filename}"] = self.tag + "\n"
            for filename in ("sums-new.txt", "sums-current.txt"):
                contents[f"{files}/pack/images/image/{filename}"] = "test-full-image\n"
        write_many(self.dut, contents)
        self.dut.run(f"tar -czf {files}/files/images.tar.gz -C {files}/pack images\n"
                     f"tar -czf {files}/files/manifests.tar.gz -C {files}/pack manifests")
        return files

    def state(self, state, files, success=True):
        # Always collect exit status explicitly; Host.run(check=False) otherwise
        # returns stdout alone and cannot distinguish a failed module invocation.
        out = self.dut.run(f"MENDER_APP_CONFIG_FILE={self.config} {APP} {state} {files} 2>&1\n"
                           "rc=$?\nprintf '\\nMODULE_EXIT=%s\\n' \"$rc\"", timeout=180)
        log, status = out.rsplit("MODULE_EXIT=", 1)
        code = int(status.strip())
        assert (code == 0) == success, f"{state} returned {code}:\n{log}"
        if self.fault_pending:
            self.dut.run(f"test ! -e {self.root}/fault")
            self.fault_pending = False
        return log

    def freeze_delta_base(self, source):
        # Exercise decoding/rollout with a reproducible base. Snapshot, load,
        # health and rollback still use the installed helper and real Docker.
        self.dut.run(f"cp {source} {self.root}/delta-base.tar")
        write(self.dut, f"{self.root}/delta-base.id", self.images["old"] + "\n")

    def transaction(self, files):
        return self.dut.run(f"cat {files}/tmp/app-transaction").strip()

    def container(self):
        ids = self.dut.run(f"docker ps -aq --filter label=com.docker.compose.project={self.name} "
                           "--filter label=com.docker.compose.service=worker").split()
        assert len(ids) == 1, ids
        return json.loads(self.dut.run(f"docker inspect {ids[0]}"))[0]

    def assert_running(self, version):
        container = self.container()
        assert container["State"]["Running"], container["State"]
        assert container["Image"] == self.images[version]
        self.dut.run(f"test -d {self.current}/manifests")

    def baseline(self, **kwargs):
        files = self.payload(**kwargs)
        self.state("ArtifactInstall", files)
        self.state("ArtifactCommit", files)
        self.state("Cleanup", files)
        return files

    def close(self):
        # Test project only; never prune global images, containers, or volumes.
        self.dut.run(f"ids=$(docker ps -aq --filter label=com.docker.compose.project={self.name}); "
                     'if test -n "$ids"; then docker rm -f $ids; fi', check=False)
        for tag in [self.tag, *self.extra_tags]:
            self.dut.run(f"docker image rm {quote(tag)}", check=False)
        self.dut.run(f"rm -rf {self.root}")


@pytest.fixture
def application(station, application_images):
    root, images = application_images
    app = Application(station, root, images)
    try:
        yield app
    finally:
        app.close()


def test_commit_keeps_backup_until_cleanup(application):
    app = application
    app.baseline()
    files = app.payload("new")
    app.state("ArtifactInstall", files)
    txn = app.transaction(files)
    app.assert_running("new")
    app.state("ArtifactCommit", files)
    app.dut.run(f"test -s {txn}/backup/images.tar && test -d {txn}/previous")
    app.state("Cleanup", files)
    app.state("Cleanup", files)
    app.dut.run(f"test ! -e {txn}")
    app.assert_running("new")


@pytest.mark.parametrize("commit", [False, True], ids=["uncommitted", "commit-leave-failure"])
def test_rollback_restores_reused_tag_and_allows_next_update(application, commit):
    app = application
    app.baseline()
    files = app.payload("new")
    app.state("ArtifactInstall", files)
    if commit:
        app.state("ArtifactCommit", files)
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    assert app.dut.run(f"docker image inspect --format '{{{{.Id}}}}' {app.tag}").strip() == app.images["old"]
    original_id = app.container()["Id"]
    app.state("ArtifactRollback", files)
    assert app.container()["Id"] == original_id, "Repeated rollback restarted the restored app"
    app.state("Cleanup", files)
    app.baseline(version="new")
    app.assert_running("new")


def test_failed_image_load_leaves_old_containers_running(application):
    app = application
    app.baseline()
    original_id = app.container()["Id"]
    files = app.payload("new", corrupt=True)
    app.state("ArtifactInstall", files, success=False)
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    assert app.container()["Id"] == original_id
    app.state("Cleanup", files)


@pytest.mark.parametrize("boundary,mode", [
    ("SNAPSHOT", "fail"), ("LOAD", "kill-after"),
    ("STOP", "kill-after"), ("ROLLOUT", "kill-before"), ("ROLLOUT", "kill-after"),
])
def test_interrupted_install_recovers(application, boundary, mode):
    app = application
    app.baseline()
    files = app.payload("new")
    app.fault(boundary, mode)
    app.state("ArtifactInstall", files, success=False)
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    app.state("Cleanup", files)


def test_failed_rollback_retains_backup_and_can_retry(application):
    app = application
    app.baseline()
    files = app.payload("new")
    app.state("ArtifactInstall", files)
    app.state("ArtifactCommit", files)
    txn = app.transaction(files)
    app.fault("RESTORE")
    app.state("ArtifactRollback", files, success=False)
    app.state("Cleanup", files)
    app.dut.run(f"test -s {txn}/backup/images.tar")
    blocked = app.payload("new")
    app.state("ArtifactInstall", blocked, success=False)
    app.state("ArtifactRollback", blocked)
    app.state("Cleanup", blocked)
    app.dut.run(f"test -s {txn}/backup/images.tar")
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    app.state("Cleanup", files)


@pytest.mark.parametrize("invalid", [False, True], ids=["started", "invalid-manifest"])
def test_first_install_rollback_removes_only_test_application(application, invalid):
    app = application
    files = app.payload(invalid=invalid)
    app.state("ArtifactInstall", files, success=not invalid)
    app.state("ArtifactRollback", files)
    app.state("ArtifactRollback", files)
    app.state("Cleanup", files)
    app.dut.run(f"test ! -e {app.current}")
    assert not app.dut.run(f"docker ps -aq --filter label=com.docker.compose.project={app.name}").strip()


def test_manifest_only_update_can_roll_back(application):
    app = application
    app.baseline()
    files = app.payload(empty=True)
    app.state("ArtifactInstall", files)
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    app.state("Cleanup", files)


@pytest.mark.parametrize("enabled,health,command,oneshot,success", [
    (False, "exit 1", None, False, True),
    (True, "exit 0", None, False, True),
    (True, "exit 1", None, False, False),
    (True, None, None, False, True),
    (True, None, ["sh", "-c", "exit 0"], True, True),
    (True, None, ["sh", "-c", "exit 1"], True, False),
    (True, None, ["sh", "-c", "exit 0"], False, False),
], ids=["disabled", "healthy", "unhealthy", "no-probe", "oneshot-ok", "oneshot-failed", "exited-daemon"])
def test_optional_health_checks(application, enabled, health, command, oneshot, success):
    app = application
    app.baseline()
    app.configure(health=enabled)
    files = app.payload("new", health=health, command=command, oneshot=oneshot)
    app.state("ArtifactInstall", files, success=success)
    if success:
        app.state("ArtifactCommit", files)
    # Both accepted and rejected updates must remain reversible before Cleanup.
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    app.state("Cleanup", files)


def test_commit_rechecks_health(application):
    app = application
    app.baseline()
    app.configure(health=True)
    files = app.payload("new", health="test ! -f /tmp/unhealthy")
    app.state("ArtifactInstall", files)
    cid = app.container()["Id"]
    app.dut.run(f"docker exec {cid} touch /tmp/unhealthy")
    # Wait for the engine health status to reflect the changed probe.
    app.dut.run(f"timeout 15 sh -c " + quote(
        f"while test \"$(docker inspect --format '{{{{.State.Health.Status}}}}' {cid})\" != unhealthy; do sleep 1; done"))
    app.state("ArtifactCommit", files, success=False)
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    app.state("Cleanup", files)


@pytest.mark.parametrize("boundary", ["STOP", "RESTORE", "ROLLOUT"])
def test_interrupted_rollback_can_retry(application, boundary):
    app = application
    app.baseline()
    files = app.payload("new")
    app.state("ArtifactInstall", files)
    app.fault(boundary, "kill-after")
    app.state("ArtifactRollback", files, success=False)
    app.state("Cleanup", files)
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    app.state("Cleanup", files)


def test_start_failure_rolls_back(application):
    app = application
    app.baseline()
    files = app.payload("new", command=["/no-such-executable"])
    app.state("ArtifactInstall", files, success=False)
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    app.state("Cleanup", files)


def test_healthcheck_waits_for_readiness(application):
    app = application
    app.configure(health=True, timeout=20)
    files = app.payload(health="test -f /tmp/ready", command=[
        "sh", "-c", "sleep 5; touch /tmp/ready; while :; do sleep 1; done"])
    app.state("ArtifactInstall", files)
    assert app.container()["State"]["Health"]["Status"] == "healthy"
    app.state("ArtifactRollback", files)
    app.state("Cleanup", files)


def test_missing_previous_container_fails_before_loading_images(application):
    app = application
    app.baseline()
    app.dut.run(f"docker rm -f {app.container()['Id']}")
    files = app.payload("new")
    app.state("ArtifactInstall", files, success=False)
    assert app.dut.run(f"docker image inspect --format '{{{{.Id}}}}' {app.tag}").strip() == app.images["old"]
    app.state("ArtifactRollback", files)
    app.state("Cleanup", files)


def test_bad_metadata_rollback_is_noop(application):
    app = application
    app.baseline()
    cid = app.container()["Id"]
    files = app.payload("new")
    write(app.dut, f"{files}/header/meta-data", '{"application_name":"../escape"}')
    app.state("ArtifactInstall", files, success=False)
    app.state("ArtifactRollback", files)
    app.state("Cleanup", files)
    assert app.container()["Id"] == cid


def test_rollback_without_image_archive_fails_and_keeps_evidence(application):
    app = application
    app.baseline()
    files = app.payload("new")
    app.state("ArtifactInstall", files)
    txn = app.transaction(files)
    app.dut.run(f"mv {txn}/backup/images.tar {txn}/backup/images.saved")
    app.state("ArtifactRollback", files, success=False)
    app.state("Cleanup", files)
    app.dut.run(f"test -d {txn}/previous && mv {txn}/backup/images.saved {txn}/backup/images.tar")
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    app.state("Cleanup", files)


@pytest.mark.parametrize("corrupt", [False, True], ids=["valid-delta", "invalid-delta"])
def test_delta_update_and_rollback(application, corrupt):
    app = application
    app.baseline()
    files = app.payload("new")
    image = f"{files}/pack/images/image"
    write(app.dut, f"{image}/url-current.txt", app.images["old"] + "\n")
    if corrupt:
        write(app.dut, f"{image}/image.img", "invalid delta\n")
    else:
        app.dut.run(f"docker image save -o {files}/base.img {app.images['old']}\n"
                    f"xdelta3 -e -s {files}/base.img {image}/image.img {files}/delta.img\n"
                    f"mv {files}/delta.img {image}/image.img", timeout=120)
        app.freeze_delta_base(f"{files}/base.img")
    app.dut.run(f"tar -czf {files}/files/images.tar.gz -C {files}/pack images")
    app.state("ArtifactInstall", files, success=not corrupt)
    if not corrupt:
        app.assert_running("new")
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    app.state("Cleanup", files)


def test_compose_environment_is_preserved_on_rollback(application):
    app = application
    old = app.payload()
    new = app.payload("new")
    for files, value in [(old, "old value with spaces $literal"), (new, "new value")]:
        metadata = dict(application_name=app.name, orchestrator="docker-compose",
                        version="1.0", platform="linux/arm64", env={"TEST_VALUE": value})
        write(app.dut, f"{files}/header/meta-data", json.dumps(metadata))
        # Inject interpolation into the payload rather than into the shell command.
        composition = dict(version="3.7", services={"worker": dict(
            image=app.tag, command=["sh", "-c", "while :; do sleep 1; done"],
            environment={"TEST_VALUE": "${TEST_VALUE}"},
            network_mode="none", stop_grace_period="1s")})
        write(app.dut, f"{files}/pack/manifests/docker-compose.yml", yaml.safe_dump(composition))
        app.dut.run(f"tar -czf {files}/files/manifests.tar.gz -C {files}/pack manifests")
    app.state("ArtifactInstall", old)
    app.state("ArtifactCommit", old)
    app.state("Cleanup", old)
    assert "TEST_VALUE=old value with spaces $literal" in app.container()["Config"]["Env"]
    app.state("ArtifactInstall", new)
    assert "TEST_VALUE=new value" in app.container()["Config"]["Env"]
    app.state("ArtifactRollback", new)
    assert "TEST_VALUE=old value with spaces $literal" in app.container()["Config"]["Env"]
    app.state("Cleanup", new)


def test_paused_service_fails_readiness(application):
    app = application
    app.baseline()
    app.configure(health=True)
    files = app.payload("new")
    app.state("ArtifactInstall", files)
    app.dut.run(f"docker pause {app.container()['Id']}")
    app.state("ArtifactCommit", files, success=False)
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    app.state("Cleanup", files)


def test_unmanaged_project_is_not_replaced(application):
    app = application
    app.dut.run(f"docker run -d --network none "
                f"--label com.docker.compose.project={app.name} "
                "--label com.docker.compose.service=worker "
                "--label com.docker.compose.oneoff=False "
                f"{app.images['old']} sh -c 'while :; do sleep 1; done'")
    cid = app.container()["Id"]
    files = app.payload("new")
    app.state("ArtifactInstall", files, success=False)
    app.state("ArtifactRollback", files)
    app.state("Cleanup", files)
    assert app.container()["Id"] == cid
    assert app.container()["State"]["Running"]



def test_all_deltas_are_decoded_before_loading_moved_tags(application):
    app = application
    app.baseline()
    files = app.payload("new")
    original_tag = app.tag
    side_tag = f"{app.name}:side"
    app.extra_tags.append(side_tag)
    app.tag = side_tag
    try:
        side = app.payload("new")
    finally:
        app.tag = original_tag
    image = f"{files}/pack/images/z-second"
    app.dut.run(f"cp -a {side}/pack/images/image {image}")
    write(app.dut, f"{image}/url-current.txt", original_tag + "\n")
    app.dut.run(f"set -e\ndocker image save -o {files}/base.img {original_tag}\n"
                f"xdelta3 -e -s {files}/base.img {image}/image.img {files}/delta.img\n"
                f"mv {files}/delta.img {image}/image.img\n"
                f"tar -czf {files}/files/images.tar.gz -C {files}/pack images", timeout=120)
    app.freeze_delta_base(f"{files}/base.img")
    app.state("ArtifactInstall", files)
    app.assert_running("new")
    assert app.dut.run(f"docker image inspect --format '{{{{.Id}}}}' {side_tag}").strip() == app.images["new"]
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    app.state("Cleanup", files)


def test_layer_delta_with_real_docker_exports(application):
    app = application
    app.baseline()
    files = app.payload("new")
    image = f"{files}/pack/images/image"
    # Encode one actual filesystem layer. The fixture images differ in config,
    # but share their Alpine layer; the source/target blob bytes are stable even
    # when the enclosing Docker export tar headers have different timestamps.
    app.dut.run(f'''set -e
mkdir -p {files}/base-unpack {files}/new-unpack
docker image save -o {files}/base.img {app.tag}
tar -xf {files}/base.img -C {files}/base-unpack
tar -xf {image}/image.img -C {files}/new-unpack
source=$(jq -r '.[0].Layers[0]' {files}/base-unpack/manifest.json)
layer=$(jq -r '.[0].Layers[0]' {files}/new-unpack/manifest.json)
xdelta3 -e -s "{files}/base-unpack/$source" "{files}/new-unpack/$layer" "{files}/new-unpack/$layer.vcdiff"
if test -f {files}/new-unpack/oci-layout; then
    printf '%s\\n' "$source" > "{files}/new-unpack/$layer.source"
else
    sha256sum "{files}/base-unpack/$source" | cut -d' ' -f1 > "{files}/new-unpack/$layer.current.sha256sum"
    sha256sum "{files}/new-unpack/$layer" | cut -d' ' -f1 > "{files}/new-unpack/$layer.new.sha256sum"
fi
rm "{files}/new-unpack/$layer"
tar -cf {image}/image.img -C {files}/new-unpack .
touch {image}/deep_delta
tar -czf {files}/files/images.tar.gz -C {files}/pack images
''', timeout=120)
    app.state("ArtifactInstall", files)
    app.assert_running("new")
    app.state("ArtifactRollback", files)
    app.assert_running("old")
    app.state("Cleanup", files)
