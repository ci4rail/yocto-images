#!/bin/bash
#
# Get checksums for github release files
# Generates snippet that can be pasted into .bb files
#
if [ "$#" -le 2 ] ; then
    echo "no arguments. Use ${0} <repo> <tag> <pattern>"
    exit 1
fi

if ! [[ -x $(which gh) ]]; then
    echo "gh tool does not exist. please download it"
    exit 1
fi

repo="$1"
tag="$2"

pattern=""
if [ "$#" -gt 2 ] ; then
    pattern="-p $3"
fi


dldir=`mktemp -d`

gh release download -D ${dldir} -R ${repo} ${tag} ${pattern}

(
    cd ${dldir}
    for file in *.tar.gz; do
        # extract arch from pathname
        a=${file%.*.*} && arch=${a##*-};
        md5sum=`md5sum $file | cut -f 1 -d ' '` 
        sha256sum=`sha256sum $file | cut -f 1 -d ' '`
        echo "SRC_URI[$arch.md5sum] = \"$md5sum\""
        echo "SRC_URI[$arch.sha256sum] = \"$sha256sum\""
    done

)

rm -rf ${dldir}
