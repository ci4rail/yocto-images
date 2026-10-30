#!/usr/bin/env bash
# CI test sequence for one station. Usage: ci-run.sh --station FILE --image TEZI_TAR [pytest args]
#
#   0 flash + secure boot sanity   (closed staging image, no customization)
#   1 default state                (functional, persistence, OTA, application tests)
#   2 internet-connected profile   (customization deployed, profile + security checks)
#   3 standalone profile           (customization replaced, profile + security checks)
#   4 default profile              (customization removed again: no leftovers)
#
# One flash, then every step reuses the target (--skip-flash). Results: results/<step>/ and
# results/<step>.xml. Later steps run even if earlier ones fail, except when flashing fails.
# SKIP_FLASH=1 reuses the installed image in step 0 as well.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"
mkdir -p results
status=0
flash_args=()
[[ -n "${SKIP_FLASH:-}" ]] && flash_args=(--skip-flash)

step() {
    local name=$1 marker=$2
    shift 2
    echo "::group::${name}"
    # -v: one line per test. The scheduler forwards complete lines only.
    PYTHONUNBUFFERED=1 ./run.sh -v "$@" -m "$marker" --results "results/${name}" --junitxml "results/${name}.xml"
    local rc=$?
    echo "::endgroup::"
    echo "step ${name}: exit ${rc}"
    return $rc
}

step 0-flash-secureboot "secureboot" ${flash_args[@]+"${flash_args[@]}"} "$@" || {
    echo "Flashing or secure boot sanity failed; skipping remaining steps"
    exit 1
}
step 1-default "hardware and not security and not secureboot and not profile and not destructive" \
    --skip-flash "$@" || status=1
step 2-internet-connected "profile or security" --skip-flash --profile internet-connected "$@" || status=1
step 3-standalone "profile or security" --skip-flash --profile standalone "$@" || status=1
step 4-default-profile "profile" --skip-flash --profile default "$@" || status=1
exit $status
