#!/bin/sh
# Install the payload embedded in this RAM root without operator interaction.
# Reboots only after TEZI reports success; on failure the RAM system stays up.
. /migration/common.sh
IMAGE=/migration/image
# TEZI's USB NCM address; its image downloader starts once usb0 has it.
ADDRESS=192.168.11.1
URL=http://$ADDRESS:8765/image.json

say() {
    echo "Migration: $*"
    echo "Migration: $*" >/dev/console
}

tezi_status() {
    /usr/bin/tezictl status 2>/dev/null || :
}

check_platform
identify_emmc
say 'Verifying embedded payload'
(cd "$IMAGE" && sha256sum -c SHA256SUMS) || fail 'Embedded payload checksum failed'

for attempt in $(seq 1 30); do
    [ -d /sys/class/net/usb0 ] && break
    sleep 1
done
[ -d /sys/class/net/usb0 ] || fail 'TEZI USB network interface is missing'
ip -4 addr show dev usb0 | grep -q "$ADDRESS/" || ip addr add "$ADDRESS/24" dev usb0 ||
    fail 'Cannot assign the local image server address'
ip link set usb0 up || fail 'Cannot enable usb0'
/migration/local-http "$IMAGE" "$ADDRESS" 8765 >"$STATE/http.log" 2>&1 &
ready=
for attempt in $(seq 1 30); do
    if wget -q -O /dev/null "$URL"; then
        ready=1
        break
    fi
    sleep 1
done
[ -n "$ready" ] || fail "Embedded image server unavailable; see $STATE/http.log"

status=
for attempt in $(seq 1 120); do
    status=$(tezi_status)
    case "$status" in idle|scanning) break ;; esac
    sleep 1
done
case "$status" in
    idle|scanning) ;;
    *) fail "TEZI control API unavailable (status: ${status:-none}); see $STATE/tezi.log" ;;
esac

say 'Installing embedded payload; the eMMC is now erased. Keep the device powered.'
# tezictl waits for the result itself; TEZI's status is checked independently.
/usr/bin/tezictl image-install "$URL" >"$STATE/tezictl.log" 2>&1 || :
while :; do
    status=$(tezi_status)
    case "$status" in
        installed) break ;;
        installing) sleep 2 ;;
        *) fail "Installation failed (TEZI status: ${status:-none}). The eMMC may be erased:" \
                "keep the device powered and collect $STATE and /var/volatile/tezi.log" ;;
    esac
done

say 'Installation complete. Rebooting into the new system in 10 seconds.'
sync
sleep 10
reboot -f
