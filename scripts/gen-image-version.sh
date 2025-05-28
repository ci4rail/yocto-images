#!/bin/bash
#
# Generate image version
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

LastTagName=$(git describe --tags --abbrev=0)
CommitsAheadTag=$(git rev-list --count ${LastTagName}..HEAD)
BranchName=$(git branch --show-current)
CommitHash=$(git rev-parse --short HEAD)

if [[ "${CommitsAheadTag}" == "0" ]]; then
    FullVerName=${LastTagName}
else
    FullVerName=${LastTagName}+${CommitsAheadTag}
fi

if [[ "${BranchName}" != "" ]]; then
    FullVerName=${FullVerName}.${BranchName}
fi

ts=`date +"%Y%m%d.%H%M"`
if [ ${is_dirty} -eq 0 ]; then
    version=${FullVerName}.${CommitHash}.${ts}
else
    version=dirty_${FullVerName}.${CommitHash}.${USER}.${ts}
fi

echo ${version}
