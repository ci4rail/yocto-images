#!/bin/bash
set -o errexit   # abort on nonzero exitstatus
set -o pipefail  # don't hide errors within pipes

useradd --shell /bin/bash -u ${USERID:-9001} -o -c "" -m user

# Give the user rights explicitly because systems like concourse CI wouldn't be able 
# to work properly as subdirs are created as root.
chown -R user:user ${WORK_DIR:-/workdir}

# Configure Git if not configured
if [ ! $(git config --global --get user.email) ]; then
    gosu user git config --global user.email "user@example.com"
    gosu user git config --global user.name "user"
    gosu user git config --global color.ui false
fi

exec gosu user "$@"