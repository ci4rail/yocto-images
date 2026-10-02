# Recovery CI

`recovery-workflow.yaml` is reusable through `workflow_call`.
Four callers select the platform and signing mode:

- `cpu01-recovery-staging.yaml`
- `cpu01-recovery-production.yaml`
- `cpu01plus-recovery-staging.yaml`
- `cpu01plus-recovery-production.yaml`

All four callers run on pushes to `secure-boot-migration-image`. Set the
repository variable `RECOVERY_MINIO_BUCKET` to an existing private MinIO bucket
for these automatic builds. Manual dispatch remains available: choose the
corresponding GitHub action, select the branch to build, and provide a bucket
through the `minio_bucket` input. The callers pass the existing
`MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` repository secrets to the reusable
workflow; environment-level recovery secrets can override those values.
No image is attached to a GitHub release or uploaded as a GitHub Actions artifact.

## Inputs and outputs

Required inputs: `platform` (`cpu01` or `cpu01plus`), `signing_mode`
(`staging` or `production`), and `minio_bucket`.
Optional `minio_prefix` defaults to `recovery` and must be one path component.
The endpoint is `https://minio.ci4rail.com`.

Objects are written under:

```text
<bucket>/<prefix>/<platform>/<signing_mode>/<version>/<git-sha>/<run-id>-<attempt>/
    recovery.itb
    build.json
    SHA256SUMS
```

The reusable workflow calculates the version with `scripts/gen-image-version.sh`,
using the repository tag, commits since that tag, short commit SHA, and build
timestamp, matching the existing image workflow's version generator. It checks
out the selected commit with full history in detached mode, so branch names do
not introduce slashes into object paths. Pinned external layer revisions are
recorded separately in `build.json`.

Workflow outputs `version`, `object_uri`, and `sha256` identify the build and image. Run and attempt
numbers distinguish retries. Engineers download using their own authenticated
MinIO access. Presigned URLs should be generated only when requested, outside CI.

## GitHub configuration

Configure `staging` and `production` environments. Restrict production to
trusted branches/tags and configure its approval policy as appropriate for your
existing signing process. Pull-request events are excluded from this workflow.

Provide these secrets through the selected environment or explicitly from the
calling workflow:

- `RECOVERY_MINIO_ACCESS_KEY`, `RECOVERY_MINIO_SECRET_KEY`: dedicated recovery
  upload credentials.
- `RECOVERY_ROOT_PASSWORD_HASH`: SHA-512 crypt root password hash, generated
  with `openssl passwd -6`. The signed image embeds the hash; it is never printed
  or saved in the build manifest. Changing the password requires rebuilding.

Production additionally uses environment variables `AZURE_CLIENT_ID`,
`AZURE_TENANT_ID`, and optionally `AZURE_SUBSCRIPTION_ID`, with the existing
Azure federated identity for GitHub's `production` environment. Azure access
needs certificate read and FIT key sign permissions; HAB keys are not involved.

Production fetches the public FIT certificate associated with the configured
Azure Key Vault key ID using the same OIDC authentication and certificate-fetch
helper as the image signing layer. No separate fingerprint variable is needed.
CI constructs a U-Boot verification DTB from the downloaded certificate and
verifies the configuration signature and image hashes with `fit_check_sign`.
The certificate's public-key fingerprint is recorded in `build.json`.

CPU01 staging uses the checked-in `dev` RSA-2048 dummy key; production uses
`moducop-cpu01-fit` RSA-3072 from Azure. CPU01plus staging uses the same dummy
FIT key, so its U-Boot must trust that key. CPU01plus production is intentionally
unconfigured: add its reviewed key ID/name/algorithm to `platforms.json` once that
platform's signing key is defined.
The workflow rejects unconfigured platform/mode combinations before building.

## MinIO configuration

Provision the destination bucket privately using an administrator account;
CI does not create buckets or alter bucket policies. CI inspects the bucket
policy and refuses any anonymous Allow statement, including conditional ones.
An absent bucket policy is accepted; an unreadable policy fails the upload.

The uploader needs `s3:GetBucketPolicy` on the bucket and `s3:PutObject` on
its recovery prefix. For example, replace `BUCKET` below with the real bucket:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:GetBucketPolicy",
      "Resource": "arn:aws:s3:::BUCKET"
    },
    {
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::BUCKET/recovery/*"
    }
  ]
}
```

For large multipart uploads, grant `s3:AbortMultipartUpload` on the same prefix
if you want failed transfers to be cleaned up automatically. Service engineers
should have separate read credentials. Downloaded images remain usable after
storage access is revoked.

## Build input provenance

`platforms.json` pins TEZI 7.7.0+build.13 URLs and SHA-256 hashes for the two
required release files: `tezi.itb` and `image.json`. These are the same files
inside the vendor release archive; no input bundle is stored in MinIO.
The kernel and RAM filesystem come from `tezi.itb`.

DTB compilation uses Linux stable commit
`d1cfde2d5d15be14123bdd1689162bd27f995a90` (6.6.143) and Toradex BSP layer commit
`0fa5efea03f13fe36fdedab229ff6a176a4b971d`, pinned by the Toradex 7.7.0
manifest. The upstream kernel recipe's ARM64 device-tree patches are applied
in recipe order, followed by the ModuCop DTS patch from `meta-ci4rail-bsp`
commit `cb4d0fa4320b3dda1bd75ad73705361d77de34dc`. All layers are fetched
explicitly; the workflow does not rely on locally populated `src/` directories. The DTB is
preprocessed and compiled with the matching source includes; no OS kernel
or full Yocto image is built. TEZI's pinned layer adds no ARM64 DT source patches.

Reference manifest:
https://git.toradex.com/cgit/toradex-manifest.git/tree/bsp/pinned-tdx.xml?h=7.7.0

The host tools container builds U-Boot v2024.07 tools. Its production target
builds the Azure SDK and PKCS#11 provider at the same source revisions and
with the same patches as the pinned `meta-akv-signing` layer. Credentials are
passed only to runtime containers, never Docker build arguments or layers.

`build.json` records input hashes, source revisions, signing mode, public-key
fingerprint, verification result, release version, and GitHub commit/run ID.
It is provenance metadata, not a boot requirement. A successful host build
still requires qualification on the target hardware.

## Local verification

```sh
docker build --target tools -t recovery-tools -f migration/ci/Dockerfile migration/ci
docker run --rm -v "$PWD:/work:ro" recovery-tools \
  python3 -m unittest discover -s migration/tests -v
```

Local tests use ephemeral dummy keys only. Production signing remains CI-only.
