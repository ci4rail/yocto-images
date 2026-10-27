#!/usr/bin/env bash
# Entry point of the CI test bundle, executed on the TC by the test-scheduler.
# Usage: test-script.sh <testId> <results-folder> <path-to-software-versions>
#
# The bundle (extracted by the scheduler, also the working directory) contains:
#   bundle.env                    STATION_CONFIG (inside the pytest package), IMAGE_FILE
#   yocto-pytest-package.tar.gz   output of tests/scripts/package.sh
#   <IMAGE_FILE>                  staging TEZI image to flash
#
# Credentials are not part of the bundle: provide TARGET_PASSWORD, DOCKER_PASSWORD and
# RELAY_UID (CPU01Plus) in the env file of the TC, default /etc/yocto-tests.env.
set -euo pipefail
test_id=${1:?testId}
results_dir=$(realpath -m "${2:?results folder}")
bundle=${TEST_SCHEDULER_TEST_BUNDLE:-$(cd "$(dirname "$0")" && pwd)}

# shellcheck disable=SC1091
source "${bundle}/bundle.env"
env_file=${YOCTO_TESTS_ENV_FILE:-/etc/yocto-tests.env}
if [[ -f "${env_file}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${env_file}"
    set +a
fi

suite="${bundle}/suite"
mkdir -p "${suite}" "${results_dir}"
tar -xzf "${bundle}/yocto-pytest-package.tar.gz" -C "${suite}"

echo "test ${test_id}: station ${STATION_CONFIG}, image ${IMAGE_FILE}"
rc=0
(cd "${suite}" && ./ci-run.sh --station "${STATION_CONFIG}" --image "${bundle}/${IMAGE_FILE}") || rc=$?

# The scheduler removes the workspace after the test: keep the results.
[[ -d "${suite}/results" ]] && cp -r "${suite}/results/." "${results_dir}/"
exit $rc
