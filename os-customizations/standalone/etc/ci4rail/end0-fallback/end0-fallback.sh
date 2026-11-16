#!/bin/sh
# Static fallback address for end0 while DHCP has no lease.
#
# NetworkManager keeps requesting a lease (end0.nmconnection: dhcp-timeout infinity).
# A DHCP address has a finite lifetime ("dynamic" in `ip addr`); this script adds the
# static FALLBACK_ADDRESS after FALLBACK_TIMEOUT seconds without one, and removes it
# as soon as a lease arrives. Run by ci4rail-end0-fallback.service.
set -u

IFACE=end0
CONF=/etc/ci4rail/end0-fallback/end0-fallback.conf
FALLBACK_GATEWAY=
FALLBACK_TIMEOUT=30
. "$CONF"
: "${FALLBACK_ADDRESS:?FALLBACK_ADDRESS missing in $CONF}"
ip_only=${FALLBACK_ADDRESS%/*}
GATEWAY_METRIC=2000  # behind any DHCP default route

now() { cut -d. -f1 /proc/uptime; }

has_lease() {
    ip -4 -o addr show dev "$IFACE" dynamic 2>/dev/null | grep -q .
}

has_fallback() {
    ip -4 -o addr show dev "$IFACE" 2>/dev/null | grep -q " inet $ip_only/"
}

add_fallback() {
    if ! has_fallback; then
        ip addr add "$FALLBACK_ADDRESS" dev "$IFACE" || return
        echo "no DHCP lease for ${FALLBACK_TIMEOUT}s: using fallback address $FALLBACK_ADDRESS"
    fi
    if [ -n "$FALLBACK_GATEWAY" ]; then
        ip route replace default via "$FALLBACK_GATEWAY" dev "$IFACE" metric "$GATEWAY_METRIC" 2>/dev/null
    fi
}

remove_fallback() {
    if [ -n "$FALLBACK_GATEWAY" ]; then
        ip route del default via "$FALLBACK_GATEWAY" dev "$IFACE" metric "$GATEWAY_METRIC" 2>/dev/null
    fi
    if has_fallback; then
        ip addr del "$FALLBACK_ADDRESS" dev "$IFACE"
        echo "${1:-DHCP lease}: removed fallback address $FALLBACK_ADDRESS"
    fi
}

if [ "${1:-}" = stop ]; then
    remove_fallback stopped
    exit 0
fi

since=$(now)
while :; do
    if has_lease; then
        remove_fallback
        since=$(now)
    elif [ $(( $(now) - since )) -ge "$FALLBACK_TIMEOUT" ]; then
        add_fallback
    fi
    sleep 2
done
