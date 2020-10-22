#!/bin/bash

set -o errexit   # abort on nonzero exitstatus
set -o pipefail  # don't hide errors within pipes

if [ -f .env ]; then
  if [ -f .env ]; then
    SSTATE_CACHE_DIR=$(cat .env | grep YOCTO_SSTATE_CACHE_DIR)
    if [[ ! -z "${SSTATE_CACHE_DIR}" ]]; then
      has_trailing_slash=$(echo "${SSTATE_CACHE_DIR}" | grep -Ec "/$" || true )
      if [ $has_trailing_slash == "1" ] ; then
        source .env
      else
        echo "Error: make sure that value for YOCTO_SSTATE_CACHE_DIR ends with  '/'"
        exit 1
      fi
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
