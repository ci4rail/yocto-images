"""Native tests of the C++ policy compiled into the protected Mender client."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAYER = ROOT / "cpu01-standard-image/src/meta-ci4rail-bsp"
FILES = LAYER / "recipes-mender/mender/files"
KEYS = ROOT / "ota-signing-material/staging"
VERIFY_KEYS = [
    "/usr/share/ci4rail/ota/ci4rail-artifact-pub.pem",
    "/data/ci4rail/ota/customer-artifact-pub.pem",
]


@pytest.fixture(scope="session")
def verifier(tmp_path_factory):
    directory = tmp_path_factory.mktemp("ota-native")
    includes = os.environ.get("MENDER_JSON_INCLUDE")
    if not includes:
        candidates = list((ROOT / "cpu01-standard-image/build/tmp/work").glob(
            "*/mender/5.1.0/package/usr/src/debug/mender/5.1.0/src/common/vendor/json/include"
        ))
        candidates = [path for path in candidates
                      if (path / "nlohmann/detail/abi_macros.hpp").exists()]
        includes = str(candidates[0]) if candidates else "/usr/include"
    source = directory / "main.cpp"
    source.write_text('''#include "ci4rail-ota-policy.hpp"
int main(int argc, char **argv) {
    try {
        if (argc < 2) return 2;
        ci4rail_ota::ValidateInvocation(argc - 1, argv + 1);
        ci4rail_ota::ValidateFiles(argv[1]);
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << std::endl;
        return 1;
    }
}
''')
    binary = directory / "verifier"
    subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-I", str(FILES), "-I", includes, str(source), "-lcrypto",
                    "-o", str(binary)], check=True)
    return binary


@pytest.fixture
def device(tmp_path):
    for name in ("usr/share/ci4rail/ota", "etc/mender", "data/mender",
                 "data/ci4rail/ota", "var/lib"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "var/lib/mender").symlink_to(tmp_path / "data/mender")
    for name in ("ci4rail-artifact-pub.pem", "ci4rail-delegation-pub.pem"):
        shutil.copyfile(KEYS / name, tmp_path / "usr/share/ci4rail/ota" / name)
    for name in ("customer-artifact-pub.pem", "customer-artifact-pub.pem.sig"):
        shutil.copyfile(KEYS / name, tmp_path / "data/ci4rail/ota" / name)
    main = {"ArtifactVerifyKeys": VERIFY_KEYS, "RootfsPartA": "/dev/mmcblk0p2"}
    fallback = {"TenantToken": "provisioned-token"}
    (tmp_path / "etc/mender/mender.conf").write_text(json.dumps(main))
    (tmp_path / "data/mender/mender.conf").write_text(json.dumps(fallback))
    (tmp_path / "usr/share/ci4rail/ota/policy.json").write_text(
        json.dumps({"main": main, "fallback": fallback}))
    return tmp_path


def run(verifier, device, *args, env=None):
    return subprocess.run([str(verifier), str(device), *args], env=env,
                          text=True, capture_output=True)


def test_valid_delegation(verifier, device):
    result = run(verifier, device)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("filename", ["customer-artifact-pub.pem", "customer-artifact-pub.pem.sig"])
def test_missing_provisioning(verifier, device, filename):
    (device / "data/ci4rail/ota" / filename).unlink()
    assert run(verifier, device).returncode == 1


@pytest.mark.parametrize("filename", ["customer-artifact-pub.pem", "customer-artifact-pub.pem.sig"])
def test_tampered_provisioning(verifier, device, filename):
    path = device / "data/ci4rail/ota" / filename
    path.write_bytes(path.read_bytes() + b"tampered")
    assert run(verifier, device).returncode == 1


@pytest.mark.parametrize("config", [
    {}, {"ArtifactVerifyKeys": []}, {"ArtifactVerifyKey": VERIFY_KEYS[0]},
    {"ArtifactVerifyKeys": [VERIFY_KEYS[0]]},
    {"ArtifactVerifyKeys": VERIFY_KEYS + ["/data/attacker.pem"]},
    {"ArtifactVerifyKeys": VERIFY_KEYS, "RootfsPartA": "/dev/attacker"},
])
def test_main_config_tampering(verifier, device, config):
    (device / "etc/mender/mender.conf").write_text(json.dumps(config))
    assert run(verifier, device).returncode == 1


@pytest.mark.parametrize("field", ["ArtifactVerifyKeys", "artifactverifykeys", "ArtifactVerifyKey"])
def test_fallback_cannot_override_keys(verifier, device, field):
    (device / "data/mender/mender.conf").write_text(json.dumps({field: []}))
    assert run(verifier, device).returncode == 1


@pytest.mark.parametrize("content", ['{', '[]', '{"TenantToken":"a","TenantToken":"b"}',
                                     '{"TenantToken":"a","tenanttoken":"b"}'])
def test_bad_json(verifier, device, content):
    (device / "data/mender/mender.conf").write_text(content)
    assert run(verifier, device).returncode == 1


def test_operational_configuration_allowed(verifier, device):
    (device / "data/mender/mender.conf").write_text(json.dumps({
        "TenantToken": "new-token", "ServerURL": "https://example.test",
        "UpdatePollIntervalSeconds": 900}))
    assert run(verifier, device).returncode == 0


@pytest.mark.parametrize("option", ["--config=x", "-c", "--fallback-config=x", "-b",
                                    "--data=x", "--datastore=x", "-d"])
def test_cli_path_overrides_rejected(verifier, device, option):
    assert run(verifier, device, option).returncode == 1


@pytest.mark.parametrize("variable", ["MENDER_CONF_DIR", "MENDER_DATA_DIR", "MENDER_DATASTORE_DIR"])
def test_environment_path_overrides_rejected(verifier, device, variable):
    assert run(verifier, device, env={**os.environ, variable: "/tmp/other"}).returncode == 1


def test_customer_key_symlink_rejected(verifier, device):
    path = device / "data/ci4rail/ota/customer-artifact-pub.pem"
    path.unlink()
    path.symlink_to(KEYS / path.name)
    assert run(verifier, device).returncode == 1


def test_parent_symlink_rejected(verifier, device):
    path = device / "data/ci4rail/ota"
    path.rename(path.with_name("elsewhere"))
    path.symlink_to(path.with_name("elsewhere"))
    assert run(verifier, device).returncode == 1


def test_datastore_redirection_rejected(verifier, device):
    path = device / "var/lib/mender"
    path.unlink()
    path.symlink_to(device / "etc/mender")
    assert run(verifier, device).returncode == 1


def test_wrong_delegation_signer(verifier, device):
    shutil.copyfile(KEYS / "ci4rail-artifact-pub.pem",
                    device / "usr/share/ci4rail/ota/ci4rail-delegation-pub.pem")
    assert run(verifier, device).returncode == 1


@pytest.mark.parametrize("signer", ["ci4rail-artifact", "customer-artifact", None])
def test_artifact_signing(tmp_path, signer):
    binary = os.environ.get("MENDER_ARTIFACT_BIN")
    if not binary:
        pytest.skip("Set MENDER_ARTIFACT_BIN to test with the Yocto native artifact tool")
    payload = tmp_path / "payload"
    payload.write_text("OTA signing integration test")
    artifact = tmp_path / "test.mender"
    command = [binary, "write", "module-image", "-T", "app", "-t", "moducop-cpu01",
               "-n", "ota-test", "-f", str(payload), "-o", str(artifact)]
    if signer:
        command += ["-k", str(KEYS / (signer + ".key.pem"))]
    subprocess.run(command, check=True, capture_output=True)
    for verifier_key in ("ci4rail-artifact", "customer-artifact"):
        result = subprocess.run([binary, "validate", "-k",
                                 str(KEYS / (verifier_key + "-pub.pem")), str(artifact)],
                                capture_output=True)
        assert (result.returncode == 0) == (signer == verifier_key)
