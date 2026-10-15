#!/usr/bin/env bash
# Build the same staging image with a different version in /etc/issue and Artifact metadata.
set -euo pipefail
[[ $# == 2 ]] || { echo 'Usage: build-core-api-smoke-image.sh cpu01|cpu01plus NEW_VERSION'; exit 1; }
case "$1" in cpu01|cpu01plus) ;; *) echo 'Unsupported platform'; exit 1 ;; esac
[[ $2 =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.+-][A-Za-z0-9_][A-Za-z0-9_.+-]*)?$ ]] || { echo 'Expected version such as v3.0.0.smoke.1'; exit 1; }
root=$(cd "$(dirname "$0")/../.." && pwd)
[[ -f "$root/Makefile" ]] || { echo 'Run this script from a repository checkout'; exit 1; }
[[ -f "$root/$1-standard-image/kasfile-sb-staging.yaml" ]] || {
    echo "No staging build configuration for $1 in this checkout; supply a staging .mender from its build environment."
    exit 1
}
# Use the explicit-version target so no source commit or production signing is needed.
exec make -C "$root" _image "IMAGE_DIR=$1-standard-image" \
    KASFILE=kasfile-sb-staging.yaml NAME_SUFFIX= "IMAGE_VERSION=$2"
