# OTA installation protection

CPU01 staging and production include OTA protection. Non-secure builds do not
include the client patch, policy, or keys. CPU01Plus secure boot remains separate
platform work. CI workflows are unchanged.

## Enforcement

The `mender_5.1.0.bbappend` in `meta-ci4rail-bsp` patches the entry point of
`mender-update`. Before update processing, the binary uses OpenSSL to verify
the customer public key's detached signature and checks both configuration files
against a policy generated at image build time. There is no shell helper or
systemd dependency that can be removed to skip the check.

The same check applies to the daemon, manual installs, and resumed operations
after client restart. It is a startup check, not continuous monitoring. An
attacker who retains unrestricted runtime root access is outside this feature's
scope. Direct execution of an attacker-supplied updater or arbitrary application
installation is not prevented by this feature.

All update operations fail if validation fails, including Core-OS updates and
commit/rollback commands. `--version` and `--help` (alone) remain available for
inventory and diagnostics. Fix provisioning/configuration before retrying an
update or resuming an interrupted deployment. Do not introduce this policy
without provisioning devices first.

Both Ci4Rail and customer artifact keys authorize every Mender artifact type.
Boot image authenticity is enforced separately by secure boot. There is no key
rotation, revocation, device binding, or rollback protection in this mechanism.

## Files

Immutable, in the dm-verity-protected rootfs:

```text
/usr/share/ci4rail/ota/policy.json
/usr/share/ci4rail/ota/ci4rail-artifact-pub.pem
/usr/share/ci4rail/ota/ci4rail-delegation-pub.pem
```

Provisioned through the os-customization layer, visible in the `/etc` overlay:

```text
/etc/ota/customer-artifact-pub.pem
/etc/ota/customer-artifact-pub.pem.sig
```

The main `/etc/mender/mender.conf` contains exactly these verification paths:

```json
{
  "ArtifactVerifyKeys": [
    "/usr/share/ci4rail/ota/ci4rail-artifact-pub.pem",
    "/etc/ota/customer-artifact-pub.pem"
  ]
}
```

This is a fragment; retain the other generated configuration fields.
`/data/mender/mender.conf` must exist and must not contain either verification
setting. `/var/lib/mender` must resolve to `/data/mender`.

All configuration fields must match their build-time values, except these
operational fields, which may be changed, added, or removed in either file:

- `TenantToken`, `ServerURL`, `Servers`
- `UpdatePollIntervalSeconds`, `InventoryPollIntervalSeconds`, `RetryPollIntervalSeconds`

JSON property names are compared case-insensitively, matching Mender. Duplicate
properties (including case variants), malformed JSON, missing files, symlinked
configuration/key files or path components, and additional protected fields are
rejected. Client path overrides through `--config`, `--fallback-config`,
`--data`/`--datastore`, their short forms, or `MENDER_*_DIR` are rejected.

When migrating an existing device, reconcile persisted `/etc` overlay content
with the new image's configuration. A stale overlaid `mender.conf` will fail
validation. Configuration defaults that change between OS releases may also
require reconciliation. Existing devices must move the customer key and signature
from `/data/ci4rail/ota` to `/etc/ota` and update the overlaid `ArtifactVerifyKeys`
path before starting the new updater. There is no fallback to the old location.

Persistent Mender configuration is excluded from factory reset in protected
builds. Include the customer key and signature in the os-customization **factory**
layer to restore them after a reset. Keys supplied only by an active customization
or writable `/etc` state may be removed by a factory reset; updates then fail
closed until the key pair is provisioned again.

## Customer delegation and provisioning

Use an RSA delegation key of at least 3072 bits. Sign the exact customer PEM
bytes using SHA-256 and RSA PKCS#1 v1.5; the `.sig` file is raw binary, not base64:

```sh
openssl dgst -sha256 -sign ci4rail-delegation.key.pem \
  -out customer-artifact-pub.pem.sig customer-artifact-pub.pem
```

Keep production delegation private material off devices and outside this
repository. The customer retains their artifact private key. Provision only
the customer public key and its signature.

Include these regular files in the os-customization payload:

```text
etc/ota/customer-artifact-pub.pem
etc/ota/customer-artifact-pub.pem.sig
```

Apply the customization through its normal lifecycle so both files are visible
at `/etc/ota` before `mender-updated` starts. Do not use symlinks for the directory
or files. During initial provisioning, use the local os-customization tooling:
a Mender-delivered customization cannot bootstrap missing keys because the
updater fails closed until they are present. Do not rewrite PEM line endings
after signing.

For staging, use the test pair in
`ota-signing-material/staging/`. These checked-in private
keys are public test material and must never be used for production. No customer
key is automatically installed in the image: staging also exercises provisioning.

Failure reasons appear on the updater's stderr, and in the journal when systemd
starts it, prefixed with `OTA protection:`. After fixing the cause, restart
`mender-updated.service` (and clear a systemd start-limit failure if necessary).

OTA keys are shared across platforms in the repository-root
`ota-signing-material/<staging|production>/` directories. The Makefile mounts
this shared directory read-only at `/ota-signing-material` in the build container.
For direct kas container invocations, provide the same mount. Platform kas files
select `CI4RAIL_OTA_KEY_ENVIRONMENT`; the common OTA include defines the key paths.

## Build and artifact signing

```sh
make IMAGE_DIR=cpu01-standard-image KASFILE=kasfile-sb-staging.yaml image
```

The staging build signs the Core-OS artifact with the staging artifact key.
The artifact is validated against the configured public key before the image
task succeeds.

For production, supply these public files before building:

```text
ota-signing-material/production/ci4rail-artifact-pub.pem
ota-signing-material/production/ci4rail-delegation-pub.pem
```

Configure `CI4RAIL_OTA_AZURE_KEY_ID` as
`https://<vault>.vault.azure.net/keys/<artifact-key>[/<version>]`. The image task
uses `mender-artifact sign --azure-key` with Azure workload identity credentials
(`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_FEDERATED_TOKEN_FILE`). The token
file must be accessible inside the build container. The signing identity needs
key get/sign permissions. The public artifact key must match the Azure key;
post-signing validation detects a mismatch. No production key IDs or public
keys have been invented or generated by this change.

Production remains intended for the CI pipeline; this change provides build
integration but does not enable or edit CI jobs. Missing signing configuration
fails the build. FIT/HAB signing remains independent of Mender artifact signing.

## Local verification

The native tests compile the actual C++ validator. Prerequisites: `g++`, OpenSSL
development headers, nlohmann JSON headers, and pytest in a virtual environment.
Set `MENDER_JSON_INCLUDE` to the directory containing `nlohmann/` if needed.

```sh
tests/.venv/bin/python -m pytest -q tests/unit/test_ota_protection.py
```

Also build `mender` using the staging kas configuration to verify Yocto patching,
cross-compilation, configuration generation, and packaging. Hardware validation
must cover boot with valid/missing/tampered provisioning, daemon and standalone
signed/unsigned installs, reboot/resume, factory reset, and non-secure behavior.
