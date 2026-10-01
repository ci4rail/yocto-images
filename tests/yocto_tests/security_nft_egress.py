"""Robot keywords for nftables egress counter checks."""

from __future__ import annotations

import re


COUNTER_PATTERN = re.compile(
    r'counter packets (?P<packets>\d+) bytes (?P<bytes>\d+).* comment "(?P<comment>[^"]+)"'
)


def parse_nft_counter_comments(nft_output: str) -> dict[str, dict[str, int]]:
    counters: dict[str, dict[str, int]] = {}
    for line in nft_output.splitlines():
        match = COUNTER_PATTERN.search(line)
        if not match:
            continue

        comment = match.group("comment")
        counter = counters.setdefault(comment, {"packets": 0, "bytes": 0})
        counter["packets"] += int(match.group("packets"))
        counter["bytes"] += int(match.group("bytes"))

    return counters


def format_nft_counters(counters: dict[str, dict[str, int]]) -> str:
    lines = []
    for comment in sorted(counters):
        counter = counters[comment]
        lines.append(
            f"{comment}: packets={counter['packets']} bytes={counter['bytes']}"
        )
    return "\n".join(lines)


def nft_counter_should_have_zero_packets(
    counters: dict[str, dict[str, int]], comment: str
) -> None:
    packets = counters.get(comment, {}).get("packets", 0)
    if packets:
        raise AssertionError(
            f"nft counter '{comment}' observed {packets} packet(s).\n"
            f"{format_nft_counters(counters)}"
        )
