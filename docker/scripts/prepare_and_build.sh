#!/bin/bash

set -o errexit   # abort on nonzero exitstatus

/usr/local/bin/prepare.sh
/usr/local/bin/build.sh