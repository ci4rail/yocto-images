"""Verify that a customer profile's os-customization took effect on the target.

Run with --profile <name>; every test skips without it. The security suite
(test_security.py) then checks the profile's baseline from the outside.
"""
import pytest

from .core_api import CoreAPI
from .customization import ENDPOINT, NAME
from .profiles import https_ok, payload_version
from .station import quote

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


def test_ntp_client(station, profile):
    only(profile, "standalone")
    server = station.cfg.get("ntp_server")
    if not server:
        pytest.skip("Set ntp_server in the station configuration to query a reachable NTP server")
    out, code = status(station.dut, f"chronyd -Q 'server {server} iburst'", timeout=120)
    assert code == 0 and "System clock wrong" in out, out


def test_dhcp_client_allowed(station, profile):
    only(profile, "standalone")
    # Replies come from udp/67 to udp/68; the request is an allowed egress.
    ruleset = station.dut.run("nft list table inet ci4rail_fw")
    assert "udp sport 67 udp dport 68 accept" in ruleset
    assert "udp sport 68 udp dport 67 accept" in ruleset
    assert "udp dport 123 accept" in ruleset


def test_default_has_no_leftovers(station, profile):
    """After resetting to the default customization nothing of a profile remains."""
    only(profile, "default")
    dut = station.dut
    dut.run("test ! -e /etc/nftables.conf && ! grep -q ci4rail-example /etc/snmp/snmpd.conf")
    dut.run("test ! -e /etc/systemd/system/tailscaled.service.d/10-disable.conf")
    dut.run("test ! -e /etc/systemd/system/avahi-daemon.service.d/10-disable.conf")
