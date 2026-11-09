# standalone: closed device without internet

USER customization installed through the core API or Mender
(`tests/config/profiles/standalone.yaml` holds the matching test expectations).

- Firewall (`etc/nftables.conf`): ingress ssh, https 443, snmpv3, ICMP. Egress DHCP,
  DNS and NTP client, ICMP.
- No mender server connection, tailscale, avahi or snmptrapd.
- SSH only as `moducop` with a key (`etc/default/dropbear`: `-s` no password
  logins, `-w` no root login). `moducop` may use `sudo` with its password.
- No login on the serial console (drop-in for `serial-getty@.service`).

The account `moducop` comes from the Core OS image (recipe `moducop-user` in
meta-ci4rail-bsp). It has no password, no key and no sudo rights until this
customization provides them.

## Replace the example credentials per customer

| File | Example content |
|---|---|
| `etc/ssh/authorized_keys/moducop` | public key of the pytest suite's test key |
| `etc/ci4rail/user-passwords/moducop` | crypt hash of `example-moducop-password` |
| `etc/snmp/snmpd.conf` | SNMPv3 user `ci4rail-example` |

Create a password hash with `openssl passwd -6`. The password itself never goes on
the device; `ci4rail-user-passwords.service` applies the hash with `chpasswd -e`
at boot, before dropbear starts.

Package the payload with root ownership, without group or other write
permissions (dropbear ignores a group-writable `authorized_keys`), with mode 0600
for `etc/ci4rail/user-passwords/*` and 0440 for `etc/sudoers.d/*`. The test suite
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
