#!/bin/bash
#
# Restore or store the Yocto downloads and sstate-cache of a CI build from/to
# the runner host's /yocto-cache.
# A lock prevents reading the cache while another job is writing to it.
#
# To be called with argument: restore|store
#

set -e

if [ "$#" -ne 1 ] ; then
    echo "Usage: ${0} restore|store"
    exit 1
fi

CACHE_DIR=/yocto-cache
LOCKFILE=${CACHE_DIR}/lock

case ${1} in
    restore|store) ;;
    *) echo "Usage: ${0} restore|store"; exit 1 ;;
esac

while ! ( set -o noclobber; echo "$$" > "${LOCKFILE}" ) 2> /dev/null; do
    echo "Cache is locked. Wait"
    sleep 60
done
trap 'rm -f "${LOCKFILE}"' EXIT
trap 'exit 1' INT TERM
echo "cache lock obtained by $$"

for dir in downloads sstate-cache; do
    if [ "${1}" = "restore" ]; then
        rsync -rht --ignore-missing-args ${CACHE_DIR}/${dir}/* ~/${dir}/
    else
        rsync -rht ~/${dir}/* ${CACHE_DIR}/${dir}/
    fi
done
echo "cache ${1} done"
