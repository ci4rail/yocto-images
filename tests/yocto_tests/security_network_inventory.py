"""Robot keywords for network service and privilege inventory checks."""

from __future__ import annotations

import re
from typing import Any


def _is_loopback_endpoint(endpoint: str) -> bool:
    local = endpoint.split("->", 1)[0]
    host = local.rsplit(":", 1)[0]
    host = host.strip("[]")
    return host in {"127.0.0.1", "::1", "localhost"} or host.startswith("127.")


def _is_service_socket(entry: dict[str, Any]) -> bool:
    protocol = entry.get("protocol")
    state = entry.get("state", "")
    endpoint = entry.get("endpoint", "")
    if not endpoint or _is_loopback_endpoint(endpoint):
        return False
    if protocol == "TCP":
        return state == "LISTEN"
    if protocol == "UDP":
        return "->" not in endpoint
    return False


def _port_from_endpoint(endpoint: str) -> int | None:
    local = endpoint.split("->", 1)[0]
    match = re.search(r":(\d+)$", local)
    if match:
        return int(match.group(1))
    return None


def parse_lsof_network_inventory(lsof_output: str) -> list[dict[str, Any]]:
    """Parse lsof field output produced by ``lsof -F pcLunPT``."""
    process: dict[str, Any] = {}
    socket: dict[str, Any] | None = None
    inventory: list[dict[str, Any]] = []

    def finish_socket() -> None:
        if socket and socket.get("protocol") and socket.get("endpoint"):
            entry = dict(process)
            entry.update(socket)
            entry["port"] = _port_from_endpoint(entry["endpoint"])
            entry["is_root"] = entry.get("uid") == 0 or entry.get("login") == "root"
            inventory.append(entry)

    for raw_line in lsof_output.splitlines():
        if not raw_line:
            continue
        field = raw_line[0]
        value = raw_line[1:]

        if field == "p":
            finish_socket()
            socket = None
            process = {"pid": int(value)}
        elif field == "c":
            process["command"] = value
        elif field == "u":
            process["uid"] = int(value)
        elif field == "L":
            process["login"] = value
        elif field == "P":
            finish_socket()
            socket = {"protocol": value}
        elif field == "n" and socket is not None:
            socket["endpoint"] = value
        elif field == "T" and socket is not None and value.startswith("ST="):
            socket["state"] = value[3:]

    finish_socket()
    return inventory


def get_service_socket_inventory(inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return externally reachable TCP listeners and UDP service sockets."""
    return [entry for entry in inventory if _is_service_socket(entry)]


def format_network_inventory(inventory: list[dict[str, Any]]) -> str:
    lines = []
    for entry in sorted(
        inventory,
        key=lambda item: (
            item.get("protocol", ""),
            item.get("port") or -1,
            item.get("command", ""),
            item.get("endpoint", ""),
        ),
    ):
        state = entry.get("state", "-")
        lines.append(
            "{protocol:3} {endpoint:35} {state:11} {login:18} "
            "pid={pid:<7} command={command}".format(
                protocol=entry.get("protocol", "-"),
                endpoint=entry.get("endpoint", "-"),
                state=state,
                login=entry.get("login", "-"),
                pid=entry.get("pid", "-"),
                command=entry.get("command", "-"),
            )
        )
    return "\n".join(lines)


def network_inventory_should_not_be_empty(inventory: list[dict[str, Any]]) -> None:
    if not inventory:
        raise AssertionError("No network service sockets were found in the DUT inventory.")


def root_network_service_processes_should_match_baseline(
    inventory: list[dict[str, Any]], allowed_processes: list[str]
) -> None:
    allowed = set(allowed_processes)
    actual = sorted(
        {
            entry["command"]
            for entry in inventory
            if entry.get("is_root") and entry.get("command")
        }
    )
    unexpected = [process for process in actual if process not in allowed]

    if unexpected:
        details = format_network_inventory(
            [entry for entry in inventory if entry.get("command") in unexpected]
        )
        raise AssertionError(
            "Unexpected root-owned network service processes: "
            f"{unexpected}. Allowed processes: {sorted(allowed)}.\n{details}"
        )
