#!/bin/sh
#
# Generate a list with all layers under <directory-with-layers>,
# determine the layer commit id and check if the layer repo is dirty.
# Write result into <output-file>
# Result will look like this:
#  bitbake: 89fc9450abefd682266efcbfa031d1ef115ff1a7
#  meta-ci.os: 39e48535e154f336b46e58813b914d92e84021d7 (dirty)

if [ "$#" -ne 2 ] ; then
    echo "no arguments. Use ${0} <directory-with-layers> <output-file>"
    exit 1
fi

set -e
if [ ! -d ${1} ]; then
    echo "${1} does not exist. Exit."
    exit 0
fi
allout=$(
    cd ${1}

    for d in *
    do
        shellout=$(
            cd $d;
            out="${d}:"
            rev=`git show -s --format=%H`
            out="${out} ${rev}"
            stat=`git status -s`
            if [ ! -z "${stat}" ]
            then
                out="${out} (dirty)"
            fi
            echo ${out}
        )
        echo "${shellout}"
    done
)
echo "${allout}" > ${2}
