"""Host-only checks of the io4edge firmware update test helpers."""
import hashlib
from types import SimpleNamespace

import pytest

from yocto_tests import test_io4edge_update as io4edge


def test_swaps_to_the_other_release_and_back():
    assert io4edge.versions_to_deploy("1.1.0") == ["1.1.1-rc.1", "1.1.0"]
    assert io4edge.versions_to_deploy("1.1.1-rc.1") == ["1.1.0", "1.1.1-rc.1"]
    # Unknown firmware: exercise both releases, end on the released one.
    assert io4edge.versions_to_deploy("1.0.1") == ["1.1.1-rc.1", "1.1.0"]


def test_cached_package_is_used_and_hash_checked(tmp_path, monkeypatch):
    data = b"firmware"
    (tmp_path / io4edge.package_name("1.1.0")).write_bytes(data)
    station = SimpleNamespace(cfg={"io4edge_firmware_dir": str(tmp_path)})
    monkeypatch.setitem(io4edge.FIRMWARE, "1.1.0", hashlib.sha256(data).hexdigest())
    assert io4edge.firmware_package(station, "1.1.0", tmp_path / "dl") == tmp_path / "fw-cpu01uc-default-1.1.0.fwpkg"
    monkeypatch.setitem(io4edge.FIRMWARE, "1.1.0", "0" * 64)
    with pytest.raises(AssertionError, match="pinned SHA-256"):
        io4edge.firmware_package(station, "1.1.0", tmp_path / "dl")


def test_missing_package_without_gh_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(io4edge.shutil, "which", lambda name: None)
    station = SimpleNamespace(cfg={"io4edge_firmware_dir": str(tmp_path)})
    with pytest.raises(AssertionError, match="GitHub CLI"):
        io4edge.firmware_package(station, "1.1.0", tmp_path)
