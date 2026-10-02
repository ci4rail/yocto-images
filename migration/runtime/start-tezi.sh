#!/bin/sh
mkdir -p -m 0700 /run/wayland
export XDG_RUNTIME_DIR=/run/wayland
export QT_WAYLAND_FORCE_DPI=72
# CPU01 has no installer display requirement; expose the UI over Ethernet VNC.
/usr/bin/weston --backend=vnc-backend.so --shell=kiosk-shell.so --no-config \
    </dev/null >/run/migration/weston.log 2>&1 &
for attempt in $(seq 1 40); do
    [ -S "$XDG_RUNTIME_DIR/wayland-0" ] && break
    sleep 1
done
if [ ! -S "$XDG_RUNTIME_DIR/wayland-0" ]; then
    echo 'Migration UI failed: inspect /run/migration/weston.log. Serial shell remains available.' >/dev/console
    exit 1
fi
/usr/bin/tezi -platform wayland </dev/null >/run/migration/tezi.log 2>&1 &
