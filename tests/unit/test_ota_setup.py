"""Signing identity and factory provisioning lifecycle checks."""
from types import SimpleNamespace

import pytest

from yocto_tests.ota import provision_factory, signing_key, sign_artifact


@pytest.mark.parametrize("kind,name", [
    ("core-os", "ci4rail-artifact"),
    ("application", "customer-artifact"),
    ("os-customization", "customer-artifact"),
])
def test_key_selection(tmp_path, kind, name):
    key = tmp_path / (name + ".key.pem")
    key.touch()
    assert signing_key({"ota_signing_material_dir": str(tmp_path)}, kind) == str(key)
    assert signing_key({}, kind) is None


def test_no_signing_material_leaves_target_unchanged():
    provision_factory(SimpleNamespace(cfg={}))
    sign_artifact(None, None, {}, "core-os")


def test_factory_provisioning_reboots_without_service_restart(tmp_path, monkeypatch):
    for name in ("customer-artifact-pub.pem", "customer-artifact-pub.pem.sig",
                 "customer-artifact.key.pem", "ci4rail-artifact.key.pem"):
        (tmp_path / name).write_text(name)
    commands, uploads = [], []
    boot_ids = iter(["before", "before", "after"])

    def run(command, **kwargs):
        commands.append(command)
        if command == "cat /proc/sys/kernel/random/boot_id":
            return next(boot_ids)
        return ""

    dut = SimpleNamespace(run=run, put=lambda src, dest: uploads.append((src.name, dest)))
    monkeypatch.setattr("yocto_tests.ota.time.sleep", lambda _: None)
    provision_factory(SimpleNamespace(cfg={"ota_signing_material_dir": str(tmp_path)}, dut=dut))
    assert any("os-customization-set install-factory" in cmd for cmd in commands)
    assert any("cp -a /data/os-customization/factory/." in cmd for cmd in commands)
    assert any("systemctl reboot" in cmd for cmd in commands)
    assert not any("restart" in cmd for cmd in commands)
    assert {name for name, _ in uploads} == {
        "customer-artifact-pub.pem", "customer-artifact-pub.pem.sig", "manifest.json"}


def test_missing_material_fails_before_target_mutation(tmp_path):
    with pytest.raises(AssertionError, match="Missing OTA provisioning file"):
        provision_factory(SimpleNamespace(cfg={"ota_signing_material_dir": str(tmp_path)}))


@pytest.mark.parametrize("kind,name", [
    ("core-os", "ci4rail-artifact"),
    ("application", "customer-artifact"),
    ("os-customization", "customer-artifact"),
])
def test_real_artifact_signature(tmp_path, kind, name):
    import shutil
    import subprocess
    from yocto_tests.test_core_api_server import STAGING_KEYS

    tool = shutil.which("mender-artifact")
    if not tool:
        pytest.skip("mender-artifact not installed")
    payload = tmp_path / "payload"
    payload.write_text("test payload")
    artifact = tmp_path / "test.mender"
    subprocess.run([tool, "write", "module-image", "-T", "app", "-t", "test-device",
                    "-n", "test", "-f", str(payload), "-o", str(artifact)],
                   capture_output=True, check=True)
    sign_artifact(tool, artifact, {"ota_signing_material_dir": str(STAGING_KEYS)}, kind)
    # CoreOS input can already carry the build's signature.
    sign_artifact(tool, artifact, {"ota_signing_material_dir": str(STAGING_KEYS)}, kind)
    for identity in ("ci4rail-artifact", "customer-artifact"):
        result = subprocess.run([tool, "validate", "-k",
                                 str(STAGING_KEYS / (identity + "-pub.pem")), str(artifact)],
                                capture_output=True)
        assert (result.returncode == 0) == (identity == name)
