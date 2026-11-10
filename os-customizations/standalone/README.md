# standalone: closed device without internet

USER customization installed through the core API or Mender
(`tests/config/profiles/standalone.yaml` holds the matching test expectations).

- Firewall (`etc/nftables.conf`): ingress ssh, https 443, snmpv3, ICMP. Egress DHCP,
  DNS and NTP client, ICMP.
- No mender server connection, tailscale, avahi or snmptrapd.
- SSH only as `moducop` or `ci4rail` with a key (`etc/default/dropbear`: `-s` no
  password logins, `-w` no root login). Both may use `sudo` with their password.
- No login on the serial console (drop-in for `serial-getty@.service`).
- end0 gets its address by DHCP. While there is no DHCP lease for 30 s, it also uses
  the static fallback address `192.168.250.1/24`, and keeps requesting a lease. When
  a lease arrives, the fallback address is removed (see below).

The accounts `moducop` and `ci4rail` come from the Core OS image (recipes
`moducop-user` and `ci4rail-user` in meta-ci4rail-bsp). They have no password, no
key and no sudo rights until a customization provides them. In production, the
factory customization provides the `ci4rail` files.

## Replace the example credentials per customer

| File | Example content |
|---|---|
| `etc/ssh/authorized_keys/moducop` | public key of the pytest suite's test key |
| `etc/ci4rail/user-passwords/moducop` | crypt hash of `example-moducop-password` |
| `etc/ssh/authorized_keys/ci4rail` | public key of the pytest suite's ci4rail test key |
| `etc/ci4rail/user-passwords/ci4rail` | crypt hash of `example-ci4rail-password` |
| `etc/snmp/snmpd.conf` | SNMPv3 user `ci4rail-example` |
| `etc/ci4rail/end0-fallback/end0-fallback.conf` | end0 fallback `192.168.250.1/24`, no gateway, 30 s |

Create a password hash with `openssl passwd -6`. The password itself never goes on
the device; `ci4rail-user-passwords.service` applies the hash with `chpasswd -e`
at boot, before dropbear starts.

Package the payload with root ownership, without group or other write
permissions (dropbear ignores a group-writable `authorized_keys`), with mode 0600
for `etc/ci4rail/user-passwords/*` and `etc/NetworkManager/system-connections/*`
(NetworkManager ignores connection files readable by others), and 0440 for
`etc/sudoers.d/*`. The test suite
does this in `tests/yocto_tests/customization.py`.

## Notes

- Once installed, root can no longer log in over SSH or on the serial console.
  Keep the private key: without it, the device can only be recovered with a new
  customization delivered through the core API or Mender, or by reflashing.
- `chpasswd` writes full copies of `/etc/shadow` and `/etc/passwd` into the
  writable state layer of `/etc`. They hide the image's files, as they already do
  when any password is changed with `passwd`.
- Kernel messages still go to the serial console. If boot fails into systemd's
  emergency or rescue mode, `sulogin` still asks for the root password there. The
  U-Boot console is not controlled by `/etc`.

## end0: DHCP with static fallback

- `etc/NetworkManager/system-connections/end0.nmconnection`: DHCP with an infinite
  timeout, so NetworkManager never stops requesting a lease.
- `ci4rail-end0-fallback.service` runs `etc/ci4rail/end0-fallback/end0-fallback.sh`.
  Every 2 s it checks for a DHCP address on end0 (an address with a lifetime,
  `dynamic` in `ip addr`). After `FALLBACK_TIMEOUT` seconds without one it adds
  `FALLBACK_ADDRESS` and, if set, a default route via `FALLBACK_GATEWAY` with
  metric 2000. As soon as a lease arrives it removes both.
- Only the address changes: the fallback provides no DNS server.
- The fallback subnet must not overlap the networks of other interfaces
  (eth2 uses `192.168.25.0/24`, the io4edge interfaces `192.168.200.0/21`).
- Change the fallback by editing `end0-fallback.conf` only.
