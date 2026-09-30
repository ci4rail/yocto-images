"""Exclusive station setup and once-per-session flashing."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from tinkerforge.ip_connection import IPConnection
from tinkerforge.bricklet_industrial_dual_relay import BrickletIndustrialDualRelay

from .station import quote

ROOT = Path(__file__).resolve().parents[1]


def power_on(cfg):
    relay = cfg["relay"]
    connection = IPConnection()
    try:
        connection.connect(relay["host"], int(relay["port"]))
        device = BrickletIndustrialDualRelay(relay["uid"], connection)
        values = list(device.get_value())
        values[int(relay["power_channel"])] = True
        device.set_value(*values)
    finally:
        connection.disconnect()



def power_cycle(cfg):
    """Boot the installed image after TEZI's warm reboot."""
    relay = cfg["relay"]
    connection = IPConnection()
    connection.connect(relay["host"], int(relay["port"]))
    try:
        device = BrickletIndustrialDualRelay(relay["uid"], connection)
        device.set_selected_value(int(relay["recovery_channel"]), False)
        device.set_selected_value(int(relay["power_channel"]), False)
        try:
            time.sleep(2)
        finally:
            device.set_selected_value(int(relay["power_channel"]), True)
    finally:
        connection.disconnect()


def flash(s, work):
    cfg = s.cfg
    image = Path(cfg.get("image_file") or "").expanduser().resolve()
    if not image.is_file():
        raise ValueError(f"TEZI archive not found: {image}")
    relay = cfg["relay"]
    if relay["power_channel"] != 0 or relay["recovery_channel"] != 1:
        raise ValueError("Installer configuration requires relay channels 0/1")
    with tempfile.TemporaryDirectory(prefix="yocto-gadget-") as temporary:
        disk = Path(temporary) / "yocto.img"
        # Conversion happens on the runner, which is either DC or TC.
        with (s.results / "gadget.log").open("w") as log:
            subprocess.run(["sudo", "-n", "bash", str(ROOT / "scripts/make-gadget-image.sh"),
                            str(image), str(disk)], check=True, stdout=log,
                           stderr=subprocess.STDOUT, timeout=cfg.get("flash_timeout", 1200))
        available = int(s.tc.run(f"df -B1 --output=avail {quote(work)} | tail -1").strip())
        required = disk.stat().st_size + 256 * 1024 * 1024
        if available < required:
            raise RuntimeError(
                f"TC work filesystem needs {required / 1024**3:.2f} GiB free "
                f"for the gadget image and reserve; only {available / 1024**3:.2f} GiB available"
            )
        s.tc.put(disk, f"{work}/yocto.img")
    power_on(cfg)
    serial = quote(cfg["serial_port"])
    for _ in range(30):
        if s.tc.run(f"test -c {serial} && echo ready", check=False).strip() == "ready":
            break
        time.sleep(1)
    else:
        raise RuntimeError("Target serial console did not appear after power-on")
    machine = {"cpu01": "verdin-imx8mm", "cpu01plus": "verdin-imx8mp"}[cfg["platform"]]
    timeout = cfg.get("flash_timeout", 1200)
    name = f"yocto-installer-{uuid.uuid4().hex}"
    command = (
        f"docker run -t --rm --privileged --network=host --name {name} "
        f"-e BRICKD_UID={quote(relay['uid'])} "
        f"-e BRICKD_IP={quote(relay['host'])} -e BRICKD_PORT={int(relay['port'])} "
        f"-v {quote(work + '/yocto.img:/mnt/yocto_image/yocto.img')} "
        f"-v {quote(cfg['serial_port'] + ':/dev/ttyTEZI')} -v /sys:/sys -v /dev:/dev "
        f"{quote(cfg['installer_image'])} -v -t {int(timeout)} -m {machine}"
    )
    try:
        output = s.tc.run(command, timeout=timeout + 60)
        if "####### SUCCESS" not in output:
            raise RuntimeError("Installer exited without its SUCCESS marker; see tc.log")
    finally:
        s.tc.run(f"docker rm -f {name}", check=False)
    power_cycle(cfg)


@contextmanager
def prepare(s, skip_flash=False):
    base = s.cfg["tc"].get("work_dir", "/var/tmp/yocto-tests")
    if not base.startswith("/") or base == "/":
        raise ValueError("tc.work_dir must be an absolute dedicated directory")
    s.tc.run(f"mkdir -p {quote(base)}")
    lock = f"{base}/station.lock"
    s.tc.run(f"mkdir {quote(lock)}")  # atomic, shared by DC and TC invocations
    work = f"{base}/run-{uuid.uuid4().hex}"
    try:
        s.tc.run(f"mkdir {quote(work)}")
        s.tc.run("docker info >/dev/null", timeout=30)
        if not skip_flash:
            flash(s, work)
        s.wait_boot()
        s.work = work
        yield
    finally:
        try:
            s.tc.run(f"rm -rf {quote(work)}", check=False)
        finally:
            s.tc.run(f"rmdir {quote(lock)}", check=False)
