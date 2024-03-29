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
#   VERSION=<fullsemver>.<branch>.<shortsha>.<datetime>
#
# To be called with arguments: <git-root> <layer-refs file>
#

set -e

if [ "$#" -ne 2 ] ; then
    echo "Usage: ${0} <git-root> <layer-refs file>"
    exit 1
fi

git_root=${1}
layer_refs=${2}

is_dirty=0

# check if the top repo is dirty
cd ${git_root}

# source the generated git versions
. gen/gitversion/env/gitversion.env

stat=`git status -s`
if [ ! -z "${stat}" ]; then
    is_dirty=1
fi

# check if one of the layer repos is dirty
if [ -f "${layer_refs}" ]; then
    if grep -q \(dirty\) ${layer_refs}; then
        is_dirty=1
    fi
fi


# replace slashes in branch name with -
branch=`echo ${GitVersion_BranchName} | sed -e "s/\//-/g"`

ts=`date +"%Y%m%d.%H%M"`
if [ ${is_dirty} -eq 0 ]; then
    version=${GitVersion_FullSemVer}.${branch}.${GitVersion_ShortSha}.${ts}
else
    version=dirty_${GitVersion_FullSemVer}.${branch}.${USER}.${ts}
fi

echo ${version}
