#!/bin/bash
set -o errexit   # abort on nonzero exitstatus
set -o pipefail  # don't hide errors within pipes

if [ -z "${WORK_DIR}" ]; then
  WORK_DIR=/workdir
  echo "No WORK_DIR defined. Defaulting to '${WORK_DIR}'"
fi

if [ -z "${DISTRIBUTION}" ]; then
  echo "No DISTRIBUTION defined. Exiting."
  exit 1
fi

if [ -z "${MACHINE}" ]; then
  echo "No MACHINE defined. Exiting."
  exit 1
fi

if [ -z "${TARGET}" ]; then
  echo "No MACHINE defined. Exiting."
  exit 1
fi

cd ${WORK_DIR}/${DISTRIBUTION}

. ./setup-environment
bitbake ${TARGET}