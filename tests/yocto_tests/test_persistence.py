"""On-target checks for persistent logs and root filesystem headroom."""
import time
import uuid

import paramiko
import pytest

from .station import quote

pytestmark = pytest.mark.hardware

MAX_LOG_BYTES = 100_000_000
MAX_JOURNAL_DISK_BYTES = 100 * 1024 * 1024
FILL_CHUNK_BYTES = 32 * 1024 * 1024


def journal_files(station):
    output = station.dut.run("find /var/log/journal -type f -name '*.journal'")
    return set(output.splitlines())


def log_bytes(station):
    return int(station.dut.run("du -sb /var/log | cut -f1").strip())


def test_persistent_logging(station):
    dut = station.dut
    assert dut.run("test -d /var/log/journal && echo yes").strip() == "yes", (
        "Persistent systemd journal directory is missing")
    marker = "yocto-pytest-" + uuid.uuid4().hex
    boot_id = dut.run("cat /proc/sys/kernel/random/boot_id").strip()
    before = journal_files(station)
    dut.run(f"printf '%s\\n' {quote(marker)} | systemd-cat -t yocto-pytest")
    dut.run("journalctl --sync")
    assert marker in dut.run("journalctl -b -t yocto-pytest --no-pager -o cat")

    dut.run("journalctl --rotate")
    after = journal_files(station)
    assert after - before, "Journal rotation did not create a new active file"
    assert log_bytes(station) <= MAX_LOG_BYTES, "/var/log exceeds 100 MB after rotation"

    # Schedule reboot after the SSH command exits so connection loss is expected.
    dut.run("nohup sh -c 'sleep 2; systemctl reboot' >/dev/null 2>&1 </dev/null &")
    deadline = time.monotonic() + station.cfg.get("boot_timeout", 300)
    while time.monotonic() < deadline:
        try:
            current = dut.run("cat /proc/sys/kernel/random/boot_id", timeout=20).strip()
            if current and current != boot_id:
                break
        except (OSError, paramiko.SSHException, RuntimeError, TimeoutError):
            pass
        time.sleep(2)
    else:
        pytest.fail("Target did not complete reboot with a new boot ID")

    previous = dut.run(f"journalctl -b {quote(boot_id.replace('-', ''))} "
                       "-t yocto-pytest --no-pager -o cat")
    assert marker in previous, "Journal entry did not survive reboot"
    assert log_bytes(station) <= MAX_LOG_BYTES, "/var/log exceeds 100 MB after reboot"


def test_journal_size_limit_under_load(station, record_property):
    dut = station.dut
    assert dut.run("test -d /var/log/journal && echo yes").strip() == "yes", (
        "Persistent systemd journal directory is missing")
    tag = "yocto-pytest-fill-" + uuid.uuid4().hex
    for index in range(4):
        # Random input prevents journal compression from making the test too easy.
        # 8 KiB base64 lines stay below journald's line limit and avoid flooding it
        # with hundreds of thousands of tiny messages.
        dut.run(f"dd if=/dev/urandom bs=1048576 count=32 2>/dev/null | "
                f"base64 -w 8192 | systemd-cat -t {quote(tag + '-' + str(index))}", timeout=300)
        dut.run("journalctl --sync", timeout=120)
        accepted = int(dut.run(
            f"journalctl -b -t {quote(tag + '-' + str(index))} --no-pager -o cat | wc -c",
            timeout=120).strip())
        assert accepted >= FILL_CHUNK_BYTES, (
            f"Journal accepted only {accepted} bytes from chunk {index}; "
            "rate limiting or dropped messages prevent a meaningful size test")

    dut.run("journalctl --rotate", timeout=120)
    dut.run("journalctl --sync", timeout=120)
    # journald's SystemMaxUse=100M limits allocated space, not apparent file size.
    used = int(dut.run("du -sB1 /var/log/journal | cut -f1").strip())
    record_property("journal_bytes_after_fill", used)
    assert used <= MAX_JOURNAL_DISK_BYTES, (
        f"Persistent journal uses {used} bytes of disk after more than 100 MB of log input")


def test_rootfs_spare_capacity(station, record_property):
    fields = station.dut.run("df -P -B1 / | tail -1").split()
    assert len(fields) == 6, f"Unexpected df output: {fields}"
    size, available = int(fields[1]), int(fields[3])
    assert size > 0
    spare_percent = available * 100 / size
    record_property("rootfs_spare_percent", spare_percent)
    assert spare_percent >= 10, f"Root filesystem has only {spare_percent:.1f}% available"
