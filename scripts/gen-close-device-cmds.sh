#!/bin/bash
#
# Generate step-by-step instructions to close a device (activate secure boot)
#
# Reads the machine-readable fuse description that meta-toradex-security
# deploys next to fuse-cmds.txt (imx-config.fuse) and prints the U-Boot
# commands in the order they must be entered on the device.
#
# To be called with arguments: <imx-config.fuse> <title>
#

set -e

if [ "$#" -ne 2 ] ; then
    echo "Usage: ${0} <imx-config.fuse> <title>"
    exit 1
fi

fuse_info=${1}
title=${2}

if ! grep -q "^H:T:HAB$" "${fuse_info}"; then
    echo "Error: ${fuse_info} does not describe a HAB device" >&2
    exit 1
fi

# H:F:<bank>:<word>:<value> lines are the SRK hash words
srk_cmds=$(awk -F: '/^H:F:/ { printf "%sfuse prog -y %s %s %s", sep, $3, $4, $5; sep = "; " }' "${fuse_info}")
# H:C:<bank>:<word>:<value> is the 'close' fuse
close_cmd=$(awk -F: '/^H:C:/ { printf "fuse prog -y %s %s %s", $3, $4, $5 }' "${fuse_info}")

if grep -q "^H:[FC]:.*:$" "${fuse_info}" || [ -z "${srk_cmds}" ] || [ -z "${close_cmd}" ]; then
    echo "Error: ${fuse_info} lacks SRK hash or close fuse values" >&2
    exit 1
fi

cat <<EOF
# ${title}
#
# Commands to close the device (activate secure boot).
#
# These are One-Time Programmable e-fuses. Once you write them you can't
# go back, so get it right the first time!

1. Enter U-Boot

2. Program SRK hashes

${srk_cmds}

3. Reboot

reset

4. Enter U-Boot again and run hab_status. It must show "No HAB Events Found!"

hab_status

   Expected output:

   Secure boot disabled

   HAB Configuration: 0xf0, HAB State: 0x66
   No HAB Events Found!

   Do NOT continue if any HAB event is reported: after closing, the SOM
   will not boot anymore.

5. Close device

${close_cmd}

After closing, the chip only loads images signed with the matching PKI
tree, and U-Boot CLI access is disabled.
EOF
