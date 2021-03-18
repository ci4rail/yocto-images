#!/bin/sh
#
# Generate a yaml file as an include file for kas
# that contains the mender artifact name and image version
#
# The following naming rules apply:
#
# If the repo or one of the the layer repos is dirty:
#   VERSION=dirty_<fullsemver>.<branch><user>.<datetime>
# Else
#   VERSION=<fullsemver>.<branch>.<shortsha>
#
# To be called with arguments: <git-root> <layer-refs file> <outfile>
#
# This script is expected to be called by dobi

set -e

if [ "$#" -ne 4 ] ; then
    echo "Usage: ${0} <git-root> <layer-refs file> <mender-artifact-prefix> <outfile>"
    exit 1
fi

git_root=${1}
layer_refs=${2}
mender_prefix=${3}
out_file=${4}

is_dirty=0

# check if the top repo is dirty
cd ${git_root}

# source the generated git versions
. gen/gitversion/env/gitversion.env

stat=`git status -s`
if [ ! -z "${stat}" ]; then
    is_dirty=1
fi
echo "top level repo is dirty: ${is_dirty} ${stat}"

# check if one of the layer repos is dirty
if grep -q \(dirty\) ${layer_refs}; then
    is_dirty=1
    echo "Layer refs are dirty"
fi

# replace slashes in branch name with -
branch=`echo ${GitVersion_BranchName} | sed -e "s/\//-/g"`

if [ ${is_dirty} -eq 0 ]; then
    version=${GitVersion_FullSemVer}.${branch}.${GitVersion_ShortSha}
else
    ts=`date +"%Y%m%d.%H%M"`
    version=dirty_${GitVersion_FullSemVer}.${branch}.${USER}.${ts}
fi

echo "header:
  version: 9
env:
  MENDER_ARTIFACT_NAME: ${mender_prefix}-${version}
  IMAGE_GIT_VERSION: ${version}" > ${out_file}

cat ${out_file}