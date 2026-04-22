#!/usr/bin/env python3
"""
Filter kernel CVEs from a Yocto image CVE report using kernel-config-aware
heuristics.

What this script does
---------------------
Yocto's ``cve-check`` output for the Linux kernel can be very noisy:

- it reports many low-impact CVEs that are only local or physical attack paths
- it reports issues for kernel subsystems that are not enabled in the built
  kernel configuration
- it may include incorrect or overly broad kernel matches

This script takes two inputs:

1. an image CVE JSON report produced by Yocto ``cve-check``
2. the built kernel ``.config`` file for the corresponding kernel

e.g.
```bash
./scripts/filter_kernel_cves.py \
  cpu01-standard-image/install/images/moducop-cpu01/Standard-Image-moducop-cpu01.json \
  cpu01-standard-image/build/tmp/work/moducop_cpu01-tdx-linux/linux-toradex/6.6.54+git/build/.config
```

It then extracts the CVEs for one kernel package (default:
``linux-toradex``), applies a set of filtering rules, and writes:

- a TSV file with the remaining kernel CVEs to review manually
- a TSV file with the excluded CVEs and the reason they were filtered out
- a BitBake ``.inc`` file with ``CVE_STATUS[...] = "not-applicable-config: ..."``
  entries for config-based exclusions only

Important limitation
--------------------
This script is intentionally conservative about the phrase:

``In the Linux kernel, the following vulnerability has been resolved:``

That phrase usually means the issue was fixed upstream, not necessarily that
your current kernel version already contains the fix. For that reason, the
script records this as the ``upstream_fix_phrase`` column, but does *not*
exclude such CVEs automatically.

How the rules work
------------------
The rules are applied in order:

1. Drop obviously incorrect matches, for example known non-kernel entries.
2. Drop CVEs for subsystems that are disabled in the kernel config.
3. Drop low/medium ``LOCAL`` and ``PHYSICAL`` CVEs, but keep high-severity
   local/physical findings for manual review.

Examples:

- ksmbd issues are excluded when ``CONFIG_SMB_SERVER`` is disabled
- NVMe/TCP issues are excluded when ``CONFIG_NVME_TCP`` is disabled
- vmwgfx issues are excluded when ``CONFIG_DRM_VMWGFX`` is disabled
- F2FS issues are excluded when ``CONFIG_F2FS_FS`` is disabled

The matching is heuristic and based on CVE summary text plus config symbols.
That means the script is intended as a repeatable triage aid, not as a formal
security proof.

Outputs
-------
The main output TSV contains these columns:

- ``cve``
- ``scorev3``
- ``vector``
- ``upstream_fix_phrase``
- ``summary``
- ``link``

The excluded TSV contains the same columns plus:

- ``filter_reason``

The generated BitBake include intentionally contains only
``not-applicable-config`` entries. It does not emit recipe metadata for:

- low/medium ``LOCAL`` / ``PHYSICAL`` vector filtering
- known bad CPE matches

That keeps the recipe metadata focused on stable, config-derived exclusions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable


Issue = dict[str, str]
ConfigMap = dict[str, str]
DEFAULT_LOCAL_PHYSICAL_MIN_SCORE = 7.0


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Filter linux kernel CVEs from a Yocto cve-check JSON report using "
            "vector and kernel config heuristics."
        )
    )
    parser.add_argument("image_json", type=Path, help="Image CVE JSON report")
    parser.add_argument("kernel_config", type=Path, help="Built kernel .config")
    parser.add_argument(
        "--package",
        default="linux-toradex",
        help="Package name inside the CVE JSON report",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("kernel_cves_filtered.tsv"),
        help="TSV file for remaining review items",
    )
    parser.add_argument(
        "--excluded-output",
        type=Path,
        default=Path("kernel_cves_excluded.tsv"),
        help="TSV file for filtered-out items with reasons",
    )
    parser.add_argument(
        "--bitbake-output",
        type=Path,
        default=Path(
            "cpu01-standard-image/src/meta-ci4rail-bsp/recipes-kernel/linux/"
            "linux-toradex-cve-status-generated.inc"
        ),
        help=(
            "BitBake include file for generated "
            'CVE_STATUS[...]="not-applicable-config: ..." entries'
        ),
    )
    parser.add_argument(
        "--local-physical-min-score",
        type=float,
        default=DEFAULT_LOCAL_PHYSICAL_MIN_SCORE,
        help=(
            "Keep LOCAL/PHYSICAL CVEs at or above this CVSSv3 score for "
            "manual review. Lower-scoring LOCAL/PHYSICAL CVEs are filtered."
        ),
    )
    return parser.parse_args()


def load_json(path: Path) -> dict:
    """Load and return a JSON document from disk."""
    with path.open() as handle:
        return json.load(handle)


def load_kernel_config(path: Path) -> ConfigMap:
    """
    Load a Linux kernel .config into a symbol/value mapping.

    Enabled symbols become ``y`` or ``m``.
    Disabled symbols become ``n``.
    """
    config: ConfigMap = {}
    with path.open() as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line.startswith("CONFIG_") and "=" in line:
                key, value = line.split("=", 1)
                config[key] = value
            elif line.startswith("# CONFIG_") and line.endswith("is not set"):
                key = line[2:].split(" ", 1)[0]
                config[key] = "n"
    return config


def config_enabled(config: ConfigMap, symbol: str) -> bool:
    """Return True when a config symbol is enabled as built-in or module."""
    return config.get(symbol) in {"y", "m"}


def issue_rows(report: dict, package_name: str, source_name: str) -> list[Issue]:
    """Extract all issue rows for the selected package from the Yocto report."""
    for package in report.get("package", []):
        if package.get("name") == package_name:
            rows = []
            for issue in package.get("issue", []):
                row = dict(issue)
                row["package"] = package_name
                row["version"] = package.get("version", "")
                rows.append(row)
            return rows
    raise SystemExit(f"Package {package_name!r} not found in {source_name}")


def summary_text(issue: Issue) -> str:
    """Return a single-line normalized summary for matching and output."""
    return issue.get("summary", "").replace("\n", " ").strip()


def has_upstream_fix_phrase(issue: Issue) -> bool:
    """
    Detect the common kernel CNA phrase that means the issue was fixed upstream.

    This is exposed as metadata only. It does not automatically exclude a CVE.
    """
    return summary_text(issue).startswith(
        "In the Linux kernel, the following vulnerability has been resolved:"
    )


def scorev3(issue: Issue) -> float:
    """Return the CVSSv3 score, or 0.0 when it is missing/unparseable."""
    try:
        return float(issue.get("scorev3", "") or 0.0)
    except ValueError:
        return 0.0


def rule_bad_cpe_match(issue: Issue, _config: ConfigMap) -> str | None:
    """Exclude entries known to be incorrect matches for the kernel package."""
    if issue.get("id") == "CVE-2023-3079":
        return "bad-cpe-match: Chromium V8 entry, not a kernel CVE"
    return None


def rule_disabled_subsystem(
    issue: Issue, config: ConfigMap, needles: tuple[str, ...], symbol: str, reason: str
) -> str | None:
    """Exclude a CVE when its subsystem is mentioned but its config is disabled."""
    text = summary_text(issue).lower()
    if any(needle in text for needle in needles) and not config_enabled(config, symbol):
        return f"not-applicable-config: {reason} ({symbol} disabled)"
    return None


def rule_disabled_subsystem_any(
    issue: Issue,
    config: ConfigMap,
    needles: tuple[str, ...],
    symbols: tuple[str, ...],
    reason: str,
) -> str | None:
    """Exclude a CVE when any of several config symbols would enable the path."""
    text = summary_text(issue).lower()
    if any(needle in text for needle in needles) and not any(
        config_enabled(config, symbol) for symbol in symbols
    ):
        return f"not-applicable-config: {reason} ({', '.join(symbols)} disabled)"
    return None


def rule_low_criticality_local_physical(
    issue: Issue, _config: ConfigMap, min_score: float
) -> str | None:
    """Filter LOCAL/PHYSICAL issues only when they are below review threshold."""
    vector = issue.get("vector", "")
    if vector in {"LOCAL", "PHYSICAL"} and scorev3(issue) < min_score:
        return f"filtered-vector-low-criticality: {vector} scorev3<{min_score:g}"
    return None


def apply_rules(issue: Issue, config: ConfigMap, local_physical_min_score: float) -> str | None:
    """
    Apply filter rules to one CVE issue.

    Returns:
    - a textual filter reason when the issue should be excluded
    - None when the issue should remain in the review set
    """
    rule_functions: list[Callable[[Issue, ConfigMap], str | None]] = [
        rule_bad_cpe_match,
        lambda i, c: rule_disabled_subsystem(
            i,
            c,
            ("memory deduplication mechanism",),
            "CONFIG_KSM",
            "KSM path disabled",
        ),
        lambda i, c: rule_disabled_subsystem(
            i,
            c,
            ("smc protocol stack", "net/smc:", " smc:"),
            "CONFIG_SMC",
            "SMC path disabled",
        ),
        lambda i, c: rule_disabled_subsystem(
            i,
            c,
            ("vmwgfx",),
            "CONFIG_DRM_VMWGFX",
            "vmwgfx driver disabled",
        ),
        lambda i, c: rule_disabled_subsystem(
            i,
            c,
            ("ksmbd",),
            "CONFIG_SMB_SERVER",
            "ksmbd/SMB server disabled",
        ),
        lambda i, c: rule_disabled_subsystem(
            i,
            c,
            ("smb: client:",),
            "CONFIG_CIFS",
            "SMB client disabled",
        ),
        lambda i, c: rule_disabled_subsystem(
            i,
            c,
            ("nvme over tcp", "nvme-tcp", "nvmet-tcp"),
            "CONFIG_NVME_TCP",
            "NVMe/TCP disabled",
        ),
        lambda i, c: rule_disabled_subsystem(
            i,
            c,
            ("nvme-rdma",),
            "CONFIG_NVME_RDMA",
            "NVMe/RDMA disabled",
        ),
        lambda i, c: rule_disabled_subsystem(
            i,
            c,
            ("f2fs:",),
            "CONFIG_F2FS_FS",
            "F2FS disabled",
        ),
        lambda i, c: rule_disabled_subsystem(
            i,
            c,
            ("lantiq_etop", "amazon-se", "danube"),
            "CONFIG_LANTIQ",
            "Lantiq ethernet path disabled",
        ),
        lambda i, c: rule_disabled_subsystem(
            i,
            c,
            ("mptcp",),
            "CONFIG_MPTCP",
            "MPTCP disabled",
        ),
        lambda i, c: rule_disabled_subsystem_any(
            i,
            c,
            ("netrom", "ax25"),
            ("CONFIG_NETROM", "CONFIG_AX25"),
            "NET/ROM path disabled",
        ),
        lambda i, c: rule_disabled_subsystem_any(
            i,
            c,
            ("tls:", "nfs over tls"),
            ("CONFIG_TLS", "CONFIG_CRYPTO_TLS"),
            "kernel TLS path disabled",
        ),
        lambda i, c: rule_disabled_subsystem_any(
            i,
            c,
            ("libceph", " ceph"),
            ("CONFIG_CEPH_LIB", "CONFIG_CEPH_FS"),
            "Ceph path disabled",
        ),
        lambda i, c: rule_disabled_subsystem_any(
            i,
            c,
            ("iscsi", "iscsit_"),
            ("CONFIG_SCSI_ISCSI_ATTRS", "CONFIG_ISCSI_TCP", "CONFIG_TARGET_ISCSI"),
            "iSCSI path disabled",
        ),
        lambda i, c: rule_disabled_subsystem(
            i,
            c,
            ("xen/netfront",),
            "CONFIG_XEN_NETDEV_FRONTEND",
            "Xen netfront disabled",
        ),
        lambda i, c: rule_disabled_subsystem(
            i,
            c,
            ("nf_conncount",),
            "CONFIG_NF_CONNCOUNT",
            "netfilter conncount path disabled",
        ),
        lambda i, c: rule_low_criticality_local_physical(
            i,
            c,
            local_physical_min_score,
        ),
    ]

    for rule in rule_functions:
        reason = rule(issue, config)
        if reason:
            return reason
    return None


def tsv_line(issue: Issue, extra: str = "") -> str:
    """Render one issue as a TSV line, optionally with an extra trailing field."""
    summary = summary_text(issue)
    fields = [
        issue.get("id", ""),
        issue.get("scorev3", ""),
        issue.get("vector", ""),
        "yes" if has_upstream_fix_phrase(issue) else "no",
        summary,
        issue.get("link", ""),
    ]
    if extra:
        fields.append(extra)
    return "\t".join(fields)


def write_lines(path: Path, header: str, lines: list[str]) -> None:
    """Write a header plus lines to a text file."""
    payload = [header, *lines]
    path.write_text("\n".join(payload) + "\n")


def quote_bitbake_string(value: str) -> str:
    """Escape a string for use inside a double-quoted BitBake assignment."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def bitbake_status_line(cve_id: str, reason: str) -> str:
    """Render one CVE_STATUS assignment for BitBake metadata."""
    return f'CVE_STATUS[{cve_id}] = "{quote_bitbake_string(reason)}"'


def write_bitbake_include(path: Path, package: str, lines: list[str]) -> None:
    """Write generated config-based CVE_STATUS entries to a BitBake include."""
    header = [
        "# Auto-generated by scripts/filter_kernel_cves.py. DO NOT EDIT BY HAND.",
        f"# Package: {package}",
        "#",
        "# This file contains config-based kernel CVE exclusions only.",
        "# Regenerate it after rebuilding the kernel and CVE report.",
        "",
    ]
    payload = header + lines
    path.write_text("\n".join(payload) + "\n")


if __name__ == "__main__":
    args = parse_args()
    report = load_json(args.image_json)
    kernel_config = load_kernel_config(args.kernel_config)
    issues = issue_rows(report, args.package, str(args.image_json))

    included: list[str] = []
    excluded: list[str] = []
    bitbake_lines: list[str] = []

    for issue in issues:
        if issue.get("status") != "Unpatched":
            continue
        reason = apply_rules(issue, kernel_config, args.local_physical_min_score)
        if reason:
            excluded.append(tsv_line(issue, reason))
            if reason.startswith("not-applicable-config: "):
                bitbake_lines.append(bitbake_status_line(issue.get("id", ""), reason))
        else:
            included.append(tsv_line(issue))

    header = "cve\tscorev3\tvector\tupstream_fix_phrase\tsummary\tlink"
    excluded_header = f"{header}\tfilter_reason"

    write_lines(args.output, header, included)
    write_lines(args.excluded_output, excluded_header, excluded)
    write_bitbake_include(args.bitbake_output, args.package, sorted(set(bitbake_lines)))

    print(
        f"kept={len(included)} excluded={len(excluded)} "
        f"output={args.output} excluded_output={args.excluded_output} "
        f"bitbake_output={args.bitbake_output}"
    )
