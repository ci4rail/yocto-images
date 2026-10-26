"""Sanity checks of the secure-boot image state on a closed staging target.

These run first, right after flashing. They do not prove enforcement of the
boot chain (that would need negative boot tests with unsigned images).
"""
import pytest

from .station import quote

pytestmark = [pytest.mark.hardware, pytest.mark.secureboot]


def test_station_uses_closed_device(station):
    assert station.cfg.get("closed") is True, \
        "CI security tests must run on a secure-boot enabled (closed) target with staging keys"


def test_rootfs_is_dm_verity_protected(station):
    source = station.dut.run("findmnt -no SOURCE /").strip()
    resolved = station.dut.run(f"readlink -f {quote(source)}").strip()
    assert resolved.startswith("/dev/dm-"), f"Root filesystem is not a device-mapper device: {source}"
    station.dut.run("test -d /sys/module/dm_verity")


def test_rootfs_is_read_only(station):
    station.dut.run("findmnt -no OPTIONS / | tr ',' '\\n' | grep -qx ro")
