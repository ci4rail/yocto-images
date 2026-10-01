"""Ports of upstream 100 through 140 Robot security suites."""
import ipaddress
import re
import time
import uuid

import pytest

from .security_abuse import (parse_nmap_grepable_open_ports,
                             build_malformed_tcp_abuse_command,
                             build_malformed_udp_abuse_command)
from .security_network_inventory import (parse_lsof_network_inventory, get_service_socket_inventory,
                                         root_network_service_processes_should_match_baseline)
from .security_nft_egress import parse_nft_counter_comments, nft_counter_should_have_zero_packets
from .station import quote

pytestmark = [pytest.mark.hardware, pytest.mark.security]


def scan(station, protocol="tcp"):
    if protocol == "tcp":
        args, timeout = "-sT -Pn --open -p-", 600
    else:
        args = "-sU -Pn --open --max-retries 1 --host-timeout 5m -p53,67,68,69,111,123,137,138,161,162,500,514,520,1900,4500,5353,5683"
        timeout = 420
    output = station.security(f"nmap {args} -oG - {quote(station.cfg['target']['host'])}", timeout)
    return parse_nmap_grepable_open_ports(output, protocol)


@pytest.mark.parametrize("protocol", ["tcp", "udp"])
def test_attack_surface(station, baseline, protocol):
    assert scan(station, protocol) == sorted(baseline[f"allowed_{protocol}_ports"])


def test_vulnerabilities(station):
    output = station.security(f"nmap -sV --script vuln -Pn --open -p- -oN - -oX - "
                              f"{quote(station.cfg['target']['host'])}", timeout=1800)
    assert not re.search(r"State:\s*VULNERABLE|VULNERABLE:|IDs:\s*CVE:|CVE-\d{4}-\d+|Exploit results:",
                         output, re.I | re.M), output


def test_service_privileges(station, baseline):
    output = station.dut.run("lsof -nP -iTCP -iUDP -F pcLunPT")
    inventory = get_service_socket_inventory(parse_lsof_network_inventory(output))
    assert inventory, "No externally reachable service sockets found"
    root_network_service_processes_should_match_baseline(inventory, baseline["allowed_root_network_processes"])


def test_egress(station, baseline):
    cfg = baseline["egress_monitor"]
    table = "yocto_" + uuid.uuid4().hex[:12]
    rules = [f"add table inet {table}",
             f"add chain inet {table} output {{ type filter hook output priority filter; policy accept; }}"]
    prefix = f"add rule inet {table} output "
    rules.append(prefix + 'meta oifname lo counter accept comment "allowed_loopback"')
    for family, key in [("ip", "allowed_ipv4_cidrs"), ("ip6", "allowed_ipv6_cidrs")]:
        for cidr in cfg[key]:
            network = ipaddress.ip_network(cidr)
            rules.append(prefix + f'{family} daddr {network} counter accept comment "allowed_{family}"')
    for proto, direction in [("tcp", "dport"), ("udp", "dport"), ("udp", "sport")]:
        for port in cfg.get(f"allowed_{proto}_{direction}s", []):
            assert 0 < int(port) < 65536
            rules.append(prefix + f'{proto} {direction} {int(port)} counter accept comment "allowed_{proto}_{direction}"')
    for proto in ("tcp", "udp", "icmp", "ipv6-icmp"):
        rules.append(prefix + f'meta l4proto {proto} counter comment "unexpected_{proto}"')
    rules.append(prefix + 'counter comment "unexpected_egress"')
    try:
        station.dut.run("nft -f -", input="\n".join(rules) + "\n")
        time.sleep(cfg["observation_seconds"])
        counters = parse_nft_counter_comments(station.dut.run(f"nft list table inet {table}"))
        assert "unexpected_egress" in counters, "Egress counter missing from nft output"
        nft_counter_should_have_zero_packets(counters, "unexpected_egress")
    finally:
        station.dut.run(f"nft delete table inet {table}", check=False)


def test_protocol_abuse(station, baseline):
    cfg = baseline["abuse_test"]
    host = quote(station.cfg["target"]["host"])
    before = scan(station)
    for port in cfg["tcp_ports"]:
        station.security(build_malformed_tcp_abuse_command(host, port, cfg["tcp_iterations"],
                                                          cfg["tcp_connect_timeout_seconds"]), timeout=120)
    for port in cfg["udp_ports"]:
        station.security(build_malformed_udp_abuse_command(host, port, cfg["udp_iterations"],
                                                          cfg["udp_payload_bytes"]), timeout=120)
    assert scan(station) == before
    station.security(f"nc -z -w {int(cfg['tcp_connect_timeout_seconds'])} {host} 22", timeout=30)
