#!/bin/sh
echo 'Migration failed. Do not reboot or power off.' >&2
echo 'Keep /run/migration/backup/mender.tar and /run/migration/*.log for recovery.' >&2
exit 0
