"""Robot keywords for bounded protocol abuse tests."""

from __future__ import annotations

import re


def parse_nmap_grepable_open_ports(nmap_output: str, protocol: str) -> list[int]:
    ports: set[int] = set()
    pattern = re.compile(rf"(\d+)/open/{re.escape(protocol)}/")
    for line in nmap_output.splitlines():
        if "Ports:" not in line:
            continue
        ports.update(int(match) for match in pattern.findall(line))
    return sorted(ports)


def port_lists_should_match(before: list[int], after: list[int], protocol: str) -> None:
    if before != after:
        raise AssertionError(
            f"{protocol} attack surface changed after abuse testing. "
            f"Before: {before}. After: {after}."
        )


def _iteration_words(iterations: int) -> str:
    return " ".join(str(index) for index in range(1, int(iterations) + 1))


def build_malformed_tcp_abuse_command(
    host: str, port: int, iterations: int, connect_timeout_seconds: int
) -> str:
    timeout = int(connect_timeout_seconds)
    port = int(port)
    rounds = _iteration_words(iterations)
    return (
        f"for i in {rounds}; do "
        f"nc -z -w {timeout} {host} {port} >/dev/null 2>&1 || true; "
        f"head -c 64 /dev/zero | nc -w {timeout} {host} {port} >/dev/null 2>&1 || true; "
        f"head -c 512 /dev/urandom | nc -w {timeout} {host} {port} >/dev/null 2>&1 || true; "
        f"head -c 2048 /dev/zero | nc -w {timeout} {host} {port} >/dev/null 2>&1 || true; "
        f"done; echo tcp_abuse_complete_port_{port}"
    )


def build_malformed_udp_abuse_command(
    host: str, port: int, iterations: int, payload_bytes: int
) -> str:
    port = int(port)
    size = int(payload_bytes)
    rounds = _iteration_words(iterations)
    return (
        f"for i in {rounds}; do "
        f"head -c 64 /dev/zero | nc -u -w 1 {host} {port} >/dev/null 2>&1 || true; "
        f"head -c 256 /dev/urandom | nc -u -w 1 {host} {port} >/dev/null 2>&1 || true; "
        f"head -c {size} /dev/zero | nc -u -w 1 {host} {port} >/dev/null 2>&1 || true; "
        f"done; echo udp_abuse_complete_port_{port}"
    )
