#!/usr/bin/env python3
"""
Generate a JSON file with all Unpatched CVEs from a Yocto image CVE report
that are not already present in the reviewed baseline file.

Default inputs:
- image report: first positional argument
- analyzed baseline: cves_analyzed.yaml
- output: unpatched_critical_cves.json

Matching rule:
- a CVE is considered already analyzed when the pair [package, id] exists in
  cves_analyzed.yaml

This keeps the comparison stable across rebuilds while still letting package
versions and CVE metadata change in the output JSON.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate unpatched_critical_cves.json from a Yocto image CVE report "
            "and the analyzed CVE baseline."
        )
    )
    parser.add_argument("image_report_json", type=Path, help="Yocto image CVE JSON report")
    parser.add_argument(
        "analyzed_yaml",
        nargs="?",
        type=Path,
        default=Path("cves_analyzed.yaml"),
        help="Reviewed CVE baseline in YAML format",
    )
    parser.add_argument(
        "output_json",
        nargs="?",
        type=Path,
        default=Path("unpatched_critical_cves.json"),
        help="Output JSON file",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict:
    with path.open() as handle:
        return json.load(handle)


def load_yaml(path: Path) -> dict:
    with path.open() as handle:
        return yaml.safe_load(handle)


def analyzed_keys(analyzed: dict) -> set[tuple[str, str]]:
    entries = analyzed.get("entries", [])
    return {
        (entry.get("package", ""), entry.get("id", ""))
        for entry in entries
    }


def unpatched_critical_entries(report: dict, reviewed: set[tuple[str, str]]) -> list[dict]:
    entries: list[dict] = []

    for pkg in report.get("package", []):
        for issue in pkg.get("issue", []):
            if issue.get("status") != "Unpatched":
                continue

            key = (pkg.get("name", ""), issue.get("id", ""))
            if key in reviewed:
                continue

            entries.append(
                {
                    "package": pkg.get("name", ""),
                    "layer": pkg.get("layer", ""),
                    "version": pkg.get("version", ""),
                    "id": issue.get("id", ""),
                    "summary": issue.get("summary", ""),
                    "scorev2": issue.get("scorev2", ""),
                    "scorev3": issue.get("scorev3", ""),
                    "scorev4": issue.get("scorev4", ""),
                    "vector": issue.get("vector", ""),
                    "vectorString": issue.get("vectorString", ""),
                    "status": issue.get("status", ""),
                    "link": issue.get("link", ""),
                }
            )

    return entries


def main() -> None:
    args = parse_args()
    report = load_json(args.image_report_json)
    analyzed = load_yaml(args.analyzed_yaml)
    reviewed = analyzed_keys(analyzed)
    entries = unpatched_critical_entries(report, reviewed)

    payload = {
        "source_report": str(args.image_report_json),
        "analyzed_baseline": str(args.analyzed_yaml),
        "matching_rule": ["package", "id"],
        "entry_count": len(entries),
        "entries": entries,
    }

    args.output_json.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {args.output_json} with {len(entries)} entries")


if __name__ == "__main__":
    main()
