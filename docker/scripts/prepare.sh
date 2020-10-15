#!/bin/bash
set -o pipefail  # don't hide errors within pipes

if [ -z "${USERID}" ]; then
  echo "No user id supplied. Using default id '9001'"
  USERID=9001
fi

if [ -z "${WORK_DIR}" ]; then
  WORK_DIR=/workdir
  echo "No WORK_DIR defined. Defaulting to '${WORK_DIR}'"
fi

if [ -z "${BUILD_DIR}" ]; then
  echo "No BUILD_DIR defined. Exiting."
  exit 1
fi

if [ -z "${DISTRIBUTION}" ]; then
  echo "No DISTRIBUTION defined. Exiting."
  exit 1
fi

if [ -z "${BRANCH}" ]; then
  echo "No BRANCH defined. Exiting."
  exit 1
fi

if [ -z "${MACHINE}" ]; then
  echo "No MACHINE defined. Exiting."
  exit 1
fi

if [ -z "${MANIFEST_REPO}" ]; then
  echo "No MANIFEST_REPO defined. Exiting."
  exit 1
fi

mkdir -p ${WORK_DIR}/${DISTRIBUTION}
cd ${WORK_DIR}/${DISTRIBUTION}

# Initialize if repo not yet initialized
repo status 2> /dev/null
if [ "$?" = "1" ]
then
    repo init -u ${MANIFEST_REPO} -b ${BRANCH}
    repo sync
fi # Do not sync automatically if repo is setup already

# Initialize build environment
source setup-environment

# Accept Freescale/NXP EULA
if ! grep -q ACCEPT_FSL_EULA conf/local.conf 
then
    echo 'ACCEPT_FSL_EULA="1"' >> conf/local.conf
fi

# Create image_list.json for Toradex Easy Installer
if [ ! -f image_list.json ]
then
    cp /etc/image_list.json image_list.json
fi