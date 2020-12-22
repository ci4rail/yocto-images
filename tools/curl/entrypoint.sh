#!/bin/sh
set -o errexit   # abort on nonzero exitstatus
set -o pipefail  # don't hide errors within pipes

addgroup -g ${GROUP_ID:-9001} user
adduser -u ${USER_ID:-9001} -G user -h /home/user -s /bin/sh -D user

exec gosu user "$@"
