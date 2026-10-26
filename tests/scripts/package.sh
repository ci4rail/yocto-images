#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
output=$(realpath -m "${1:-yocto-pytest-package.tar.gz}")
# Explicit allowlist excludes station secrets, production keys, venvs and results.
# The repository's dummy staging OTA key is deliberately bundled for staging smoke tests.
tar -czf "$output" --exclude=__pycache__ --exclude='*.pyc' -C "$root" \
    README.md requirements.txt pytest.ini conftest.py run.sh ci-run.sh yocto_tests \
    config/station.example.yaml config/security_baseline.yaml config/docker-compose.yaml \
    config/profiles config/station-ci-cpu01.yaml config/station-ci-cpu01plus.yaml \
    scripts/make-gadget-image.sh scripts/build-core-api-smoke-image.sh \
    -C "$root/.." os-customizations \
    -C "$root/../ota-signing-material/staging" \
    --transform='s,^\(ci4rail-artifact[^/]*\|customer-artifact[^/]*\)$,staging-ota-keys/\1,' \
    ci4rail-artifact.key.pem ci4rail-artifact-pub.pem \
    customer-artifact.key.pem customer-artifact-pub.pem customer-artifact-pub.pem.sig
