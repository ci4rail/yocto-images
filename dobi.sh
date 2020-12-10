#!/bin/bash

set -o errexit   # abort on nonzero exitstatus
set -o pipefail  # don't hide errors within pipes

if [ -f .env ]; then
  source .env
  if [[ ! -z "${YOCTO_SSTATE_CACHE_DIR}" ]]; then
    if [[ "${YOCTO_SSTATE_CACHE_DIR}" != *\/ ]]; then
      echo "Error: make sure that value for YOCTO_SSTATE_CACHE_DIR ends with  '/'"
      exit 1
    fi
  fi
fi

# check argument count
if [ -z ${1} ]; then
    echo "no arguments!"
    dobi --filename meta.yaml list
    exit 1
fi

# execute dobi with meta as default
exec dobi --filename meta.yaml ${@}
