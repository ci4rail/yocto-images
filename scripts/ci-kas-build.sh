#!/bin/bash
#
# Build the Yocto image in ${IMAGE_DIR} with kas inside the ${BUILDER_IMAGE} container (CI only).
# Build outputs go to ~/install, caches to ~/sstate-cache and ~/downloads.
#
# Expects in the environment: GITHUB_WORKSPACE, IMAGE_DIR, BUILDER_IMAGE, MENDER_SERVER_URL,
# MENDER_TENANT_TOKEN, IMAGE_NAME_SUFFIX, IMAGE_GIT_VERSION, MENDER_ARTIFACT_NAME
#
# To be called with arguments: <kasfile> [additional docker run arguments...]
#

set -e

if [ "$#" -lt 1 ] ; then
    echo "Usage: ${0} <kasfile> [additional docker run arguments...]"
    exit 1
fi

kasfile=${1}
shift

docker run \
    -v ~/install:/install \
    -v ~/sstate-cache:/sstate-cache \
    -v ~/downloads:/downloads \
    -v ${GITHUB_WORKSPACE}/${IMAGE_DIR}:/work \
    -v ${GITHUB_WORKSPACE}/kas-includes:/kas-includes \
    -v ${GITHUB_WORKSPACE}/ota-signing-material:/ota-signing-material:ro \
    -v ${GITHUB_WORKSPACE}/secure-boot-material:/secure-boot-material:ro \
    -u $(id -u):$(id -g) \
    -e MENDER_SERVER_URL \
    -e MENDER_TENANT_TOKEN \
    -e IMAGE_NAME_SUFFIX \
    -e IMAGE_GIT_VERSION \
    -e MENDER_ARTIFACT_NAME \
    -e GITCONFIG_FILE=/kas-includes/gitconfig \
    "$@" \
    -w /work \
    ${BUILDER_IMAGE} \
    build ${kasfile}
