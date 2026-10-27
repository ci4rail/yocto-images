"""Exclusive station setup and once-per-session flashing."""
from contextlib import contextmanager
from pathlib import Path
import json
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


def recovery_config(cfg):
    """recovery.yaml for the signed-recovery tdx-installer (selects one exact bundle)."""
    rec = cfg["recovery"]
    lines = [
        "recovery:",
        f"  endpoint: {json.dumps(rec['endpoint'])}",
        f"  bucket: {json.dumps(rec['bucket'])}",
        f"  platform: {json.dumps(cfg['platform'])}",
        f"  mode: {json.dumps(rec['mode'])}",
        f"  version: {json.dumps(rec['version'])}",
        f"  run-attempt: {json.dumps(rec['run_attempt'])}",
        "  cache-folder: /mnt/recovery",
    ]
    return "\n".join(lines) + "\n"


def write_recovery_files(s, work):
    """Create recovery.yaml and the secret files in the TC work dir (removed after the run)."""
    rec = s.cfg["recovery"]
    s.tc.run(f"cat > {quote(work + '/recovery.yaml')}", input=recovery_config(s.cfg))
    # Secrets are written on stdin: never on a command line and not in tc.log.
    s.tc.run(f"umask 077 && cat > {quote(work + '/recovery.env')}", secret=True, input=(
        f"AWS_ACCESS_KEY_ID={rec['access_key']}\nAWS_SECRET_ACCESS_KEY={rec['secret_key']}\n"))
    s.tc.run(f"umask 077 && cat > {quote(work + '/recovery-password')}", secret=True,
             input=str(rec["root_password"]))


def flash(s, work):
    cfg = s.cfg
    image = Path(cfg.get("image_file") or "").expanduser().resolve()
    if not image.is_file():
        raise ValueError(f"TEZI archive not found: {image}")
    relay = cfg["relay"]
    if relay["power_channel"] != 0 or relay["recovery_channel"] != 1:
        raise ValueError("Installer configuration requires relay channels 0/1")
    # The conversion extracts the TEZI tar and builds an image 1.5x its size: far more than a
    # RAM-backed /tmp (1.9 GiB on the TC) holds. On the TC use its work dir, which is disk-backed.
    scratch = work if s.tc.local else None
    with tempfile.TemporaryDirectory(prefix="yocto-gadget-", dir=scratch) as temporary:
        disk = Path(temporary) / "yocto.img"
        # Conversion happens on the runner, which is either DC or TC.
        with (s.results / "gadget.log").open("w") as log:
            subprocess.run(["sudo", "-n", "env", f"TMPDIR={temporary}", "bash",
                            str(ROOT / "scripts/make-gadget-image.sh"),
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
    timeout = cfg.get("flash_timeout", 1200)
    name = f"yocto-installer-{uuid.uuid4().hex}"
    if "recovery" in cfg:
        # Signed-recovery installer: downloads the exact recovery bundle from MinIO.
        write_recovery_files(s, work)
        recovery = (
            f"--env-file {quote(work + '/recovery.env')} "
            "-e TEZI_ROOT_PASSWORD_FILE=/run/secrets/tezi-root-password "
            f"--mount type=bind,src={quote(work + '/recovery-password')},"
            "dst=/run/secrets/tezi-root-password,readonly "
            f"-v {quote(work + '/recovery.yaml:/config/recovery.yaml:ro')} "
            f"-v {quote(cfg['recovery'].get('cache_volume', 'recovery-cache'))}:/mnt/recovery "
        )
        installer_args = f"-v -t {int(timeout)}"
    else:
        machine = {"cpu01": "verdin-imx8mm", "cpu01plus": "verdin-imx8mp"}[cfg["platform"]]
        recovery = ""
        installer_args = f"-v -t {int(timeout)} -m {machine}"
    command = (
        f"docker run -t --rm --privileged --network=host --name {name} "
        f"-e BRICKD_UID={quote(relay['uid'])} "
        f"-e BRICKD_IP={quote(relay['host'])} -e BRICKD_PORT={int(relay['port'])} "
        f"{recovery}"
        f"-v {quote(work + '/yocto.img:/mnt/yocto_image/yocto.img')} "
        f"-v {quote(cfg['serial_port'] + ':/dev/ttyTEZI')} -v /sys:/sys -v /dev:/dev "
        f"{quote(cfg['installer_image'])} {installer_args}"
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
