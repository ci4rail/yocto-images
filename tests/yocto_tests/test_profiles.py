"""Verify that a customer profile's os-customization took effect on the target.

Run with --profile <name>; every test skips without it. The security suite
(test_security.py) then checks the profile's baseline from the outside.
"""
import ipaddress
import re
import time

import paramiko
import pytest

from .core_api import CoreAPI
from .customization import ENDPOINT, NAME
from .profiles import https_ok, key_users, payload_version
from .setup import power_cycle
from .station import LOGIN_KEYS, Host, quote

pytestmark = [pytest.mark.hardware, pytest.mark.profile]

PROBE_OID = "1.3.6.1.2.1.1.1.0"  # sysDescr.0


def only(profile, *names):
    if profile is None:
        pytest.skip("Run with --profile")
    if profile["name"] not in names:
        pytest.skip(f"Only for profile {', '.join(names)}")


def status(host, command, timeout=60):
    """Run a command, returning (combined output, exit status)."""
    out = host.run(f"{command} 2>&1\nprintf '\\nEXIT=%s' $?", timeout=timeout, check=False)
    log, code = out.rsplit("\nEXIT=", 1)
    return log, int(code)


def test_customization_installed(station, profile):
    only(profile, "internet-connected", "standalone", "default")
    expected = {"name": NAME, "version": payload_version(profile)}
    assert CoreAPI(station).current(ENDPOINT) == expected
    assert station.dut.run("cat /etc/ci4rail-profile").strip() == profile["name"]


def test_services(station, profile):
    only(profile, "internet-connected", "standalone", "default")
    dut = station.dut
    for name in profile.get("services_active", []):
        assert dut.run(f"systemctl is-active {quote(name)}", check=False).strip() == "active", name
    for name in profile.get("services_inactive", []):
        assert dut.run(f"systemctl is-active {quote(name)}", check=False).strip() != "active", name


def test_firewall(station, profile):
    only(profile, "internet-connected", "standalone", "default")
    _, code = status(station.dut, "nft list table inet ci4rail_fw")
    assert (code == 0) == (profile["name"] != "default"), "Firewall table state does not match the profile"


def test_icmp_allowed(station, profile):
    only(profile, "internet-connected", "standalone")
    out, code = status(station.tc, f"ping -c 3 -W 2 {quote(station.cfg['target']['host'])}")
    assert code == 0, out


def test_internet_reachability(station, profile):
    only(profile, "internet-connected")
    reach, failures = profile["reachability"], []
    for host in reach["dns"]:
        out, code = status(station.dut, f"getent hosts {quote(host)}")
        if code:
            failures.append(f"DNS {host}: {out}")
    for url in reach["https"]:
        out, code = status(station.dut, f"curl -sS -o /dev/null --max-time 20 --write-out '%{{http_code}}' {quote(url)}")
        if code or not out.strip().endswith(("200", "301", "302", "401", "403", "404")):
            failures.append(f"HTTPS {url}: {out}")
    assert not failures, "\n".join(failures)


def test_https_service(station, profile):
    only(profile, "standalone")
    https = profile["https"]
    out, code = status(station.tc, f"curl -ksS --max-time 10 https://{quote(station.cfg['target']['host'])}:{int(https['port'])}/")
    assert code == 0 and https["marker"] in out, out
    assert https_ok(station.dut, https)


SNMP_IMAGE = "yocto-tests/snmp-tools:1"


def snmpget(station, profile, *args):
    """Query the target from the TC with net-snmp in a container (nothing to install on the TC)."""
    station.tc.run(f"docker image inspect {SNMP_IMAGE} >/dev/null 2>&1 || docker build -q -t {SNMP_IMAGE} -",
                   input="FROM alpine:3.20\nRUN apk add --no-cache net-snmp-tools\n", timeout=300)
    return status(station.tc, f"docker run --rm --network=host --entrypoint snmpget {SNMP_IMAGE} -t 3 -r 1 "
                  + " ".join(args) + f" {quote(station.cfg['target']['host'])} {PROBE_OID}")


def v3_args(cfg, **override):
    cfg = {**cfg, **override}
    return ["-v3", "-l", "authPriv", "-u", quote(cfg["user"]),
            "-a", cfg["auth_protocol"], "-A", quote(cfg["auth_passphrase"]),
            "-x", cfg["priv_protocol"], "-X", quote(cfg["priv_passphrase"])]


def test_snmpv3_authpriv_works(station, profile):
    only(profile, "standalone")
    out, code = snmpget(station, profile, *v3_args(profile["snmpv3"]))
    assert code == 0 and "STRING" in out, out


@pytest.mark.parametrize("community", ["public", "private"])
def test_snmp_v1_v2c_rejected(station, profile, community):
    only(profile, "standalone")
    for version in ("1", "2c"):
        _, code = snmpget(station, profile, "-v" + version, "-c", community)
        assert code != 0, f"SNMP v{version} with community {community!r} was answered"


def test_snmpv3_wrong_credentials_rejected(station, profile):
    only(profile, "standalone")
    for override in ({"auth_passphrase": "wrong-auth-passphrase"}, {"priv_passphrase": "wrong-priv-passphrase"},
                     {"user": "nobody"}):
        _, code = snmpget(station, profile, *v3_args(profile["snmpv3"], **override))
        assert code != 0, f"SNMPv3 request with {override} was answered"


def test_snmpv3_without_privacy_rejected(station, profile):
    only(profile, "standalone")
    cfg = profile["snmpv3"]
    _, code = snmpget(station, profile, "-v3", "-l", "authNoPriv", "-u", quote(cfg["user"]),
                      "-a", cfg["auth_protocol"], "-A", quote(cfg["auth_passphrase"]))
    assert code != 0, "SNMPv3 without encryption was answered"


def dhcp_ntp_servers(dut):
    """NTP servers the NetworkManager dispatcher wrote from DHCP option 42."""
    out, _ = status(dut, "cat /run/chrony-dhcp/*.sources")
    return [line.split()[1] for line in out.splitlines() if line.startswith("server ")]


def test_ntp_client(station, profile):
    only(profile, "standalone")
    dut = station.dut
    if station.cfg.get("ntp_from_dhcp"):
        # Whole chain: DHCP option 42 -> NM dispatcher -> chrony sourcedir -> firewall egress -> sync.
        servers = dhcp_ntp_servers(dut)
        assert servers, "DHCP provided no NTP server (/run/chrony-dhcp/*.sources missing or empty)"
        out, code = status(dut, "chronyc -n sources")
        assert code == 0 and all(server in out for server in servers), out
        out, code = status(dut, "chronyc -n waitsync 24 0 0 5", timeout=180)
        assert code == 0, f"chronyd did not synchronize to {servers}:\n{out}"
        tracking = dut.run("chronyc -n tracking")
        assert any(f"({server})" in tracking for server in servers), tracking
        return
    server = station.cfg.get("ntp_server")
    if not server:
        pytest.skip("Set ntp_from_dhcp or ntp_server in the station configuration")
    out, code = status(dut, f"chronyd -Q 'server {server} iburst'", timeout=120)
    assert code == 0 and "System clock wrong" in out, out


def test_client_egress_allowed(station, profile):
    only(profile, "standalone")
    # DHCP replies come from udp/67 to udp/68; the request is an allowed egress.
    ruleset = station.dut.run("nft list table inet ci4rail_fw")
    assert "udp sport 67 udp dport 68 accept" in ruleset
    assert "udp sport 68 udp dport 67 accept" in ruleset
    assert "udp dport 53 accept" in ruleset and "tcp dport 53 accept" in ruleset, "DNS client"
    assert "udp dport 123 accept" in ruleset


def target_host(station, login, name):
    """A DUT host that only tries `login`."""
    config = {k: v for k, v in station.cfg["target"].items() if k not in LOGIN_KEYS}
    return Host({**config, **login}, station.results, name, jump=station.dut.jump)


def login_accepted(station, login):
    try:
        target_host(station, login, "dut-login-check").connect().close()
    except paramiko.AuthenticationException:
        return False
    return True


def test_ssh_only_key_users(station, profile):
    only(profile, "standalone")
    users = key_users(profile)
    station_login = {k: v for k, v in station.cfg["target"].items() if k in LOGIN_KEYS}
    assert not login_accepted(station, station_login), "The station's (root) login is still accepted"
    failures = []
    for access in users:
        name = access["username"]
        if not login_accepted(station, access):
            failures.append(f"{name} cannot log in with its key")
        if login_accepted(station, {**access, "username": "root"}):
            failures.append(f"root accepts the key of {name}")
        if login_accepted(station, {"username": name, "password": access["sudo_password"]}):
            failures.append(f"Password login is accepted for {name}")
        for other in users:
            if other is not access and login_accepted(station, {**access, "key_file": other["key_file"]}):
                failures.append(f"{name} accepts the key of {other['username']}")
    assert not failures, "\n".join(failures)


def test_sudo_with_password(station, profile):
    only(profile, "standalone")
    for access in key_users(profile):
        name = access["username"]
        user = target_host(station, {**access, "sudo_password": None}, f"dut-{name}")
        assert user.run("id -un").strip() == name
        with pytest.raises(RuntimeError):
            user.run("sudo -n true")
        with pytest.raises(RuntimeError):
            target_host(station, {**access, "sudo_password": "wrong-password"}, f"dut-{name}-wrong-sudo").run("true")
        assert target_host(station, access, f"dut-{name}-sudo").run("id -un").strip() == "root", name
    assert station.dut.run("id -un").strip() == "root"


def test_no_serial_console_login(station, profile):
    only(profile, "standalone")
    dut = station.dut
    units = dut.run("systemctl list-units --all --plain --no-legend 'serial-getty@*'", check=False)
    assert not [line for line in units.splitlines() if " active " in line], units
    # getty@tty1 on the (unused) virtual terminal is fine: the device has no graphics output.
    # getty@.service passes "-" as tty on the command line and opens /dev/ttyN via
    # TTYPath=, so look at the terminal the process actually has on stdin.
    gettys = dut.run(
        "for p in $(pgrep getty); do echo \"$(readlink /proc/$p/fd/0) $(tr '\\0' ' ' </proc/$p/cmdline)\"; done",
        check=False,
    ).strip().splitlines()
    serial = [line for line in gettys if not re.match(r"/dev/tty\d+ ", line)]
    assert not serial, f"A serial console getty is running: {serial}"


def test_default_has_no_leftovers(station, profile):
    """After resetting to the default customization nothing of a profile remains."""
    only(profile, "default")
    dut = station.dut
    dut.run("test ! -e /etc/nftables.conf && ! grep -q ci4rail-example /etc/snmp/snmpd.conf")
    dut.run("test ! -e /etc/systemd/system/tailscaled.service.d/10-disable.conf")
    dut.run("test ! -e /etc/systemd/system/avahi-daemon.service.d/10-disable.conf")
    for user in ("moducop", "ci4rail"):
        dut.run(f"test ! -e /etc/sudoers.d/{user} && test ! -e /etc/ssh/authorized_keys/{user}"
                f" && test ! -e /etc/ci4rail/user-passwords/{user}")
    dut.run("test ! -e /etc/systemd/system/serial-getty@.service.d/10-disable.conf")
    dut.run("grep -qx 'DROPBEAR_EXTRA_ARGS=\"\"' /etc/default/dropbear")


# Drops DHCP replies on end0. A netdev ingress hook: NetworkManager's DHCP client
# receives on a packet socket, which the inet input hook does not see. Not
# persistent: a reboot removes it.
DHCP_BLOCK = """table netdev yocto_test_dhcp_block {
    chain ingress {
        type filter hook ingress device "end0" priority -500;
        ip protocol udp udp sport 67 udp dport 68 counter drop
    }
}
"""
UNREACHABLE = (OSError, EOFError, paramiko.SSHException, RuntimeError, TimeoutError)


def wait_for(check, timeout, interval=3):
    """Call check until it returns something true; None after timeout."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            result = check()
        except UNREACHABLE:
            result = None
        if result or time.monotonic() > deadline:
            return result or None
        time.sleep(interval)


def end0_ipv4(host):
    return host.run("ip -4 -o addr show dev end0")


def blocked_replies(host):
    out = host.run("nft list chain netdev yocto_test_dhcp_block ingress")
    return int(re.search(r"counter packets (\d+)", out).group(1))


def test_end0_dhcp_with_static_fallback(station, profile):
    """end0 takes the static fallback address only while DHCP has no lease.

    Blocks DHCP replies on the target, reaches it on the fallback address
    through the TC, then unblocks and waits for the DHCP address to return.
    """
    only(profile, "standalone")
    fallback = profile["end0_fallback"]
    address = ipaddress.ip_interface(fallback["address"])
    dut, tc = station.dut, station.tc
    marker = f" inet {address.with_prefixlen} "

    lease = end0_ipv4(dut)
    assert " dynamic " in lease, f"end0 has no DHCP address: {lease}"
    assert marker not in lease, f"Fallback address is used despite a DHCP lease: {lease}"

    target = tc.run(f"getent ahostsv4 {quote(station.cfg['target']['host'])}").split()[0]
    tc_dev = re.search(r" dev (\S+)", tc.run(f"ip -o route get {quote(target)}")).group(1)
    tc_address = f"{quote(fallback['tc_address'])} dev {quote(tc_dev)}"
    config = {k: v for k, v in station.cfg["target"].items() if k not in LOGIN_KEYS}
    # The fallback subnet is only reachable from the TC.
    at_fallback = Host({**config, **dut.login, "host": str(address.ip)}, station.results,
                       "dut-fallback", jump=tc)

    tc.run(f"sudo -n ip addr add {tc_address}")
    try:
        dut.run("nft -f -", input=DHCP_BLOCK)
        # Restart DHCP; the old lease and with it this SSH connection go away.
        dut.run("nohup sh -c 'sleep 2; nmcli connection up end0' </dev/null >/dev/null 2>&1 &")
        state = wait_for(lambda: (lambda a: marker in a and a)(end0_ipv4(at_fallback)),
                         fallback["timeout"] + 90)
        assert state, f"Target not reachable on its fallback address {address.ip}"
        assert " dynamic " not in state, state
        # DHCP keeps trying: the router's answers to new requests keep arriving.
        first = blocked_replies(at_fallback)
        assert wait_for(lambda: blocked_replies(at_fallback) > first, 150, interval=5), \
            "No further DHCP replies: the target stopped requesting a lease"

        at_fallback.run("nft delete table netdev yocto_test_dhcp_block")
        assert wait_for(lambda: dut.run("true", timeout=20) == "", 180, interval=5) is not None, \
            "Target did not get its DHCP address back"
        state = wait_for(lambda: (lambda a: marker not in a and a)(end0_ipv4(dut)), 30)
        assert state, "Fallback address stays after the DHCP lease returned"
        assert " dynamic " in state, state
    finally:
        tc.run(f"sudo -n ip addr del {tc_address}", check=False)
        if wait_for(lambda: dut.run("true", timeout=20) == "", 10) is None:
            # Unreachable on the DHCP address: a reboot drops the DHCP block.
            power_cycle(station.cfg)
            station.wait_boot()
        else:
            dut.run("nft delete table netdev yocto_test_dhcp_block 2>/dev/null", check=False)
