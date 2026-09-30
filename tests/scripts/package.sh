#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
output=$(realpath -m "${1:-yocto-pytest-package.tar.gz}")
# Explicit allowlist prevents inclusion of station secrets, venvs and results.
tar -czf "$output" --exclude=__pycache__ --exclude='*.pyc' -C "$root" \
    README.md requirements.txt pytest.ini conftest.py run.sh yocto_tests \
    config/station.example.yaml config/security_baseline.yaml config/docker-compose.yaml \
    scripts/make-gadget-image.sh
