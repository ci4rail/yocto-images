"""Ports of upstream 005 through 090 Robot suites."""
import json
from pathlib import Path
import re
import time
import uuid

import paramiko
import pytest

from .station import quote

pytestmark = pytest.mark.hardware


def test_ssh_authentication(station):
    cfg = station.cfg["target"]
    password = cfg.get("password")
    assert password, "Password authentication tests require target.password"
    for user, value, success in [("def", password, False), (cfg["username"], password, True),
                                 (cfg["username"], password + "1", False),
                                 (cfg["username"], "", False), (cfg["username"], "root", False)]:
        # Explicitly disable key/agent fallback, including for empty passwords.
        with paramiko.SSHClient() as client:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(cfg["host"], port=cfg.get("port", 22), username=user,
                               password=value, allow_agent=False, look_for_keys=False,
                               timeout=15, auth_timeout=15)
            except paramiko.AuthenticationException:
                assert not success, "Valid password was rejected"
            else:
                assert success, "Invalid credentials were accepted"


def test_docker_login(station):
    password = station.cfg.get("docker_password")
    assert password, "docker_password is missing from station configuration"
    station.dut.run(f"docker login --username {quote(station.cfg['docker_username'])} --password-stdin",
                    input=password + "\n", secret=True, timeout=120)


def test_busybox(station):
    station.dut.run("docker run --rm docker.io/library/busybox:latest", timeout=180)


def test_compose(station):
    project = "yoctotest" + uuid.uuid4().hex[:10]
    remote = f"/tmp/{project}.yaml"
    station.dut.put(Path(__file__).parents[1] / "config/docker-compose.yaml", remote)
    cmd = f"docker compose -p {project} -f {remote}"
    try:
        station.dut.run(f"{cmd} up -d", timeout=240)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            logs = station.dut.run(f"{cmd} logs tester")
            if "If you see this page, nginx is successfully installed and working." in logs:
                break
            time.sleep(2)
        else:
            pytest.fail("Compose client did not reach nginx")
    finally:
        station.dut.run(f"{cmd} down --volumes", timeout=90, check=False)
        station.dut.run(f"rm -f {remote}", check=False)


def test_gpsd_from_container(station, feature):
    feature("gnss")
    output = station.dut.run("docker run --rm docker.io/library/busybox:latest "
                             "timeout 10 nc 172.17.0.1 2947", timeout=120, check=False)
    assert re.search(r'"class"\s*:\s*"VERSION"', output)


def iperf(station, server, client, address, minimum, record_property, prefix="", bind=None, seconds=10):
    for host in (server, client):
        host.run("command -v iperf3 >/dev/null || { echo 'Missing iperf3; provision it on this host' >&2; exit 1; }")
    port = 5201
    logfile = f"/tmp/yocto-iperf-{uuid.uuid4().hex}.log"
    pid = server.run(f"nohup {prefix}iperf3 -s --one-off -p {port} >{logfile} 2>&1 </dev/null & echo $!").strip()
    assert pid.isdigit(), "Could not start iperf server"
    try:
        time.sleep(2)
        binding = f" --bind-dev={quote(bind)}" if bind else ""
        output = client.run(f"iperf3 -J -c {quote(address)} -p {port} --connect-timeout 1000 "
                            f"--time {seconds}{binding}", timeout=seconds + 30)
        result = json.loads(output)
        assert "error" not in result, result.get("error")
        for direction in ("sum_sent", "sum_received"):
            bps = result["end"][direction]["bits_per_second"]
            record_property(f"iperf_{direction}_mbps", bps / 1_000_000)
            assert bps > minimum * 1_000_000, result["end"]
    finally:
        server.run(f"kill {pid} 2>/dev/null || true; cat {logfile}; rm -f {logfile}", check=False)


def test_ethernet1(station, record_property):
    iperf(station, station.dut, station.tc, station.cfg["target"]["host"], 900, record_property)


def test_ethernet2(station, feature, record_property):
    feature("ethernet2")
    dut = station.dut
    interface = dut.run(f"ls {quote(station.cfg['eth2_path'] + '/net')}").strip()
    assert re.fullmatch(r"[\w.-]+", interface), "Expected one ETH2 interface"
    ns = "yocto" + uuid.uuid4().hex[:8]
    dut.run(f"ip netns add {ns}")
    try:
        dut.run(f"ip link set {quote(interface)} netns {ns}")
        dut.run(f"ip netns exec {ns} ip link set {quote(interface)} up")
        dut.run(f"ip netns exec {ns} udhcpc -n -q -t 10 -i {quote(interface)}", timeout=90)
        addresses = json.loads(dut.run(f"ip netns exec {ns} ip -j -4 addr show dev {quote(interface)}"))
        address = next(a["local"] for a in addresses[0]["addr_info"] if a["scope"] == "global")
        iperf(station, dut, station.tc, address, station.cfg["eth2_mbps"], record_property,
              prefix=f"ip netns exec {ns} ")
    finally:
        dut.run(f"ip netns exec {ns} ip link set {quote(interface)} netns 1", check=False)
        dut.run(f"ip netns del {ns}", check=False)
        dut.run(f"ip link set {quote(interface)} up", check=False)


def test_gnss(station, feature):
    feature("gnss")
    deadline = time.monotonic() + 700
    while time.monotonic() < deadline:
        output = station.dut.run(
            "docker run --rm docker.io/library/busybox:latest sh -c "
            + quote("{ printf '?WATCH={\"enable\":true,\"json\":true}\\n'; sleep 15; } "
                    "| timeout 20 nc 172.17.0.1 2947"),
            timeout=60, check=False)
        for line in output.splitlines():
            try:
                report = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (report.get("class") == "TPV" and report.get("mode", 0) >= 3
                    and isinstance(report.get("lat"), (int, float))
                    and isinstance(report.get("lon"), (int, float))):
                return
        time.sleep(2)
    pytest.fail("No 3D GNSS position within 700 seconds")


def test_modem(station, feature, record_property):
    feature("modem")
    dut, cfg = station.dut, station.cfg
    assert cfg["modem_model"] in dut.run("mmcli -L")
    name = "yocto-lte-" + uuid.uuid4().hex[:8]
    try:
        dut.run(f"nmcli c add type gsm ifname '*' con-name {name} apn {quote(cfg['sim_apn'])}")
        deadline = time.monotonic() + 300
        last = "No attempt completed"
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            print(f"LTE attempt {attempt}: connecting", flush=True)
            try:
                dut.run(f"nmcli -w 60 c up {name}", timeout=70)
                output = dut.run(f"ping -I {quote(cfg['modem_device'])} -c 50 -i 0.3 -W 3 www.google.com",
                                 timeout=180, check=False)
                loss = re.search(r"([\d.]+)% packet loss", output)
                rtt = re.search(r"= [\d.]+/([\d.]+)/", output)
                if loss:
                    record_property("lte_packet_loss_percent", float(loss[1]))
                if rtt:
                    record_property("lte_mean_rtt_ms", float(rtt[1]))
                print(f"LTE attempt {attempt}: packet loss={loss[1] + '%' if loss else 'unavailable'}, "
                      f"mean RTT={rtt[1] + ' ms' if rtt else 'unavailable'}", flush=True)
                if loss and rtt and float(loss[1]) < 50 and float(rtt[1]) < 1500:
                    return
                last = output
            except (RuntimeError, TimeoutError) as exc:
                last = str(exc)
                detail = last.strip().splitlines()[-1]
                print(f"LTE attempt {attempt}: {detail}", flush=True)
            time.sleep(2)
        dut.run("uptime; journalctl -u NetworkManager --no-pager -n 100", check=False)
        pytest.fail(f"LTE connectivity did not meet the baseline within 300 seconds: {last}")

    finally:
        dut.run(f"nmcli c delete {name}", check=False)


def fio(station, path, read, write, mounts, record_property, size="100M"):
    size_arg = f"--size={size}" if size else ""
    output = station.dut.run(f"docker run --rm --privileged {mounts} {quote(station.cfg['testing_image'])} "
                            f"fio --rw=readwrite --name={quote(path)} {size_arg} --runtime=10 "
                            "--direct=1 --output-format=json", timeout=210)
    result = json.loads(output)
    for index, job in enumerate(result["jobs"]):
        record_property(f"fio_job_{index}_read_bytes_per_second", job["read"]["bw_bytes"])
        record_property(f"fio_job_{index}_write_bytes_per_second", job["write"]["bw_bytes"])
        assert job.get("error", 0) == 0, job
        assert job["read"]["bw_bytes"] > read, job
        assert job["write"]["bw_bytes"] > write, job


def test_emmc(station, record_property):
    assert re.search(r"/dev/mmcblk[02]p\d+ on /data ", station.dut.run("mount"))
    path = "/data/yocto-fio-" + uuid.uuid4().hex
    try:
        fio(station, path, station.cfg["emmc_read_bps"], station.cfg["emmc_write_bps"],
            "-v /data:/data", record_property)
    finally:
        station.dut.run(f"rm -f {path}", check=False)


@pytest.mark.destructive
def test_sdcard(station, feature, record_property):
    feature("sdcard")
    cfg, dut = station.cfg, station.dut
    device = cfg["sdcard"]
    assert re.fullmatch(r"/dev/mmcblk\d+", device), "Expected a whole SD block device"
    # Require an actual removable card and reject mounted root/data ancestors.
    block = Path(device).name
    assert dut.run(f"cat /sys/class/block/{block}/removable").strip() == "1"
    mounts = dut.run(f"lsblk -nr -o MOUNTPOINT {quote(device)}")
    assert not any(p.strip() in {"/", "/data", "/boot"} for p in mounts.splitlines())
    dut.run(f"test -b {quote(device)}")
    dut.run(f"for p in {quote(device)}p*; do umount \"$p\" 2>/dev/null || true; done")
    fio(station, device, cfg["sdcard_read_bps"], cfg["sdcard_write_bps"],
        "-v /dev:/dev", record_property, size=None)
    dut.run(f"dd if=/dev/zero of={quote(device)} count=100000", timeout=90)
    dut.run(f"parted -s {quote(device)} mklabel msdos mkpart primary fat32 1MiB 100%")
    dut.run(f"mkfs.vfat -F32 {quote(device + 'p1')}")
    dut.run("mount -a")
    assert re.search(re.escape(device + "p1") + r" .*sdcard", dut.run("mount"))


def test_wifi(station, feature, record_property):
    feature("wifi")
    cfg, dut = station.cfg, station.dut
    antenna = cfg["wifi_antenna"].split()
    assert len(antenna) == 2 and all(re.fullmatch(r"0x[0-9a-fA-F]+", a) for a in antenna)
    name = "yocto-wifi-" + uuid.uuid4().hex[:8]
    try:
        dut.run(f"iw phy phy0 set antenna {' '.join(antenna)}")
        dut.run(f"nmcli -w 60 device wifi connect {quote(cfg['wifi_ssid'])} "
                f"password {quote(cfg['wifi_password'])} ifname {quote(cfg['wifi_device'])} name {name}",
                secret=True, timeout=70)
        iperf(station, station.tc, dut, cfg["tc"]["host"], 5, record_property,
              bind=cfg["wifi_device"], seconds=45)
    finally:
        dut.run(f"nmcli c delete {name}", check=False)


def test_serial(station, feature):
    feature("serial")
    output = station.dut.run("avahi-browse _ttynvt._tcp -t")
    assert station.cfg["serial_devices"], "No serial devices configured"
    for device in station.cfg["serial_devices"]:
        assert device.removeprefix("tty") in output
        station.dut.run(f"docker run --rm --privileged -v /dev:/dev {quote(station.cfg['testing_image'])} "
                        f"linux-serial-test -p {quote('/dev/' + device)} -b 115200 -x 5000 -v 15000 "
                        "-I 5000 -w 1024 --no-icount --ascii --stats --dump-err --stop-on-err --error-on-timeout",
                        timeout=100)


def test_io4edge(station, feature):
    feature("io4edge")
    addresses = station.dut.run("ip a")
    services = station.dut.run("avahi-browse _io4edge-core._tcp -t")
    for address in station.cfg["io4edge_ips"]:
        assert address in addresses
        station.dut.run(f"ping -c 3 -W 5 {quote(address)}", timeout=30)
    for name in station.cfg["io4edge_devices"]:
        assert name in services
        station.dut.run(f"ping -c 3 -W 5 {quote(name + '.local')}", timeout=30)
