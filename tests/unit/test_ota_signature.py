"""Invalid OTA signatures built for the update rejection tests."""
import shutil
import subprocess

import pytest

from yocto_tests.customization import build_customization
from yocto_tests.ota import BAD_SIGNATURES, assert_signature_invalid, break_signature, signing_material

CFG = {"ota_signing_material_dir": "staging"}


@pytest.fixture
def tool():
    path = shutil.which("mender-artifact")
    if not path:
        pytest.skip("mender-artifact not installed")
    return path


def validate(tool, artifact, key):
    return subprocess.run([tool, "validate", "-k", signing_material(CFG) / key, artifact],
                          capture_output=True, text=True).returncode


@pytest.mark.parametrize("mode", BAD_SIGNATURES)
def test_break_signature(tool, tmp_path, mode):
    artifact = build_customization(tool, "v1", "moducop-cpu01", tmp_path, CFG)
    assert validate(tool, artifact, "customer-artifact-pub.pem") == 0
    break_signature(tool, artifact, mode)
    assert_signature_invalid(tool, artifact, CFG)
    read = subprocess.run([tool, "read", artifact], capture_output=True, text=True)
    assert read.returncode == 0 and "Name: v1" in read.stdout
    assert ("no signature" in read.stdout) == (mode == "unsigned")


def test_assert_signature_invalid_detects_valid_artifact(tool, tmp_path):
    artifact = build_customization(tool, "v1", "moducop-cpu01", tmp_path, CFG)
    with pytest.raises(AssertionError, match="still validates"):
        assert_signature_invalid(tool, artifact, CFG)
