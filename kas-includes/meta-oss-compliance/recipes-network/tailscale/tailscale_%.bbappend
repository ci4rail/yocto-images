# The upstream Tailscale client is BSD-3-Clause.  The base recipe installs a
# binary release and incorrectly marks it CLOSED, so attach the exact tagged
# corresponding source and redistributable license to the compliance output.
LICENSE = "BSD-3-Clause"
LIC_FILES_CHKSUM = "file://${WORKDIR}/tailscale-LICENSE;md5=a672713a9eb730050e491c92edf7984d"

SRC_URI:append = " \
    git://github.com/tailscale/tailscale.git;protocol=https;nobranch=1;name=source;destsuffix=tailscale-source \
    file://tailscale-LICENSE \
"
SRCREV_source = "dec88625eafdcac4dfae8f592705919184ec4df7"
SRCREV_FORMAT = "source"

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
