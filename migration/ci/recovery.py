#!/usr/bin/env python3
"""CI orchestration for recovery; run inside the accompanying tools container."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).with_name('platforms.json')
BSP_PATCH = Path('recipes-kernel/linux/linux-toradex/0001-moducop-specific-dts.patch')
AKV = Path('/opt/meta-akv-signing/scripts')


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def settings(platform, mode):
    config = json.loads(CONFIG.read_text())
    profile = config['platforms'][platform]
    signing = profile['signing'].get(mode)
    if not signing:
        raise ValueError(f'{platform}/{mode} has no reviewed signing configuration in {CONFIG.name}')
    return config, profile, signing


def object_prefix(prefix, platform, mode, version, commit, run_id, attempt):
    for part in (prefix, version, commit, run_id, attempt):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', part) or part in ('.', '..'):
            raise ValueError('Invalid output path component')
    return f'{prefix}/{platform}/{mode}/{version}/{commit}/{run_id}-{attempt}'


def validate_environment(mode):
    for name in ('MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY', 'RECOVERY_ROOT_PASSWORD_HASH'):
        if not os.environ.get(name):
            raise ValueError(f'Missing required secret: {name}')
    bucket = os.environ.get('MINIO_BUCKET', '')
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket) or '..' in bucket:
        raise ValueError('Invalid MinIO bucket name')
    with tempfile.TemporaryDirectory() as directory:
        password_file = Path(directory) / 'root.hash'
        password_file.write_text(os.environ['RECOVERY_ROOT_PASSWORD_HASH'])
        password_file.chmod(0o600)
        build.read_root_password_hash(password_file)
    if mode == 'production':
        for name in ('AZURE_CLIENT_ID', 'AZURE_TENANT_ID'):
            if not os.environ.get(name):
                raise ValueError(f'Missing production variable: {name}')


def fetch(url, sha256, destination):
    if not re.fullmatch(r'[0-9a-f]{64}', sha256) or not url.startswith('https://'):
        raise ValueError('Downloads require HTTPS and a pinned SHA-256')
    with urllib.request.urlopen(url, timeout=60) as response, destination.open('wb') as target:
        if not response.url.startswith('https://'):
            raise ValueError('Download redirected away from HTTPS')
        shutil.copyfileobj(response, target)
    if build.digest(destination) != sha256:
        destination.unlink()
        raise ValueError(f'Checksum mismatch for {url}')


def checkout(url, revision, directory, sparse=()):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Source revision must be a full commit ID')
    run('git', 'init', directory, stdout=subprocess.DEVNULL)
    run('git', '-C', directory, 'remote', 'add', 'origin', url)
    run('git', '-C', directory, 'fetch', '--depth=1', '--filter=blob:none', 'origin', revision)
    if sparse:
        run('git', '-C', directory, 'sparse-checkout', 'set', *sparse)
    run('git', '-C', directory, 'checkout', '--detach', 'FETCH_HEAD')


def prepare(config, profile, work):
    tezi = work / 'tezi'
    tezi.mkdir()
    for name, checksum in profile['tezi_sha256'].items():
        fetch(f"{profile['tezi_url']}/{name}", checksum, tezi / name)
    if json.loads((tezi / 'image.json').read_text())['version'] != config['tezi_version']:
        raise ValueError('Unexpected TEZI version')
    kernel, bsp = work / 'linux', work / 'bsp'
    checkout('https://github.com/gregkh/linux.git', config['kernel_revision'], kernel,
             ('arch/arm64/boot/dts', 'include/dt-bindings', 'include/uapi'))
    checkout('https://git.toradex.com/meta-toradex-bsp-common.git', config['bsp_revision'], bsp)
    recipes = bsp / 'recipes-kernel/linux'
    recipe = (recipes / 'linux-toradex-upstream_6.6.bb').read_text()
    if f'SRCREV_machine = "{config["kernel_revision"]}"' not in recipe:
        raise ValueError('Kernel revision does not match pinned BSP recipe')
    # Preserve the recipe's patch order. Only DT sources are needed for cpp/dtc.
    for name in re.findall(r'file://([^\s]+\.patch)', recipe):
        candidates = [recipes / directory / name for directory in
                      ('linux-toradex-upstream-6.6', 'linux-toradex-upstream')]
        patch = next((p for p in candidates if p.is_file()), None)
        if patch is None:
            raise ValueError(f'Missing BSP patch: {name}')
        if '+++ b/arch/arm64/boot/dts/' in patch.read_text():
            run('git', '-C', kernel, 'apply', '--include=arch/arm64/boot/dts/*.dts',
                '--include=arch/arm64/boot/dts/*.dtsi', patch)
    ci4rail = work / 'ci4rail-bsp'
    checkout('https://github.com/ci4rail/meta-ci4rail-bsp.git',
             config['ci4rail_bsp_revision'], ci4rail, ('recipes-kernel/linux',))
    run('git', '-C', kernel, 'apply', ci4rail / BSP_PATCH)
    dts = kernel / 'arch/arm64/boot/dts/freescale' / (profile['dts'] + '.dts')
    preprocessed = work / 'board.dts'
    with preprocessed.open('w') as target:
        run('aarch64-linux-gnu-gcc', '-E', '-nostdinc', '-undef', '-D__DTS__',
            '-x', 'assembler-with-cpp', '-I', kernel / 'include',
            '-I', kernel / 'arch/arm64/boot/dts', dts, stdout=target)
    run('dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', work / 'board.dtb', preprocessed)


def trust_tree(certificate, keyname, algorithm, destination):
    """Construct the U-Boot public-key node, independently of the signing tool."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    cert = x509.load_pem_x509_certificate(certificate.read_bytes())
    key = cert.public_key()
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValueError('FIT certificate must contain an RSA public key')
    bits = int(algorithm.split('rsa')[1])
    if key.key_size != bits:
        raise ValueError('FIT key size does not match signing algorithm')
    spki = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    fingerprint = hashlib.sha256(spki).hexdigest()
    numbers = key.public_numbers()
    def cells(value, length):
        return ' '.join(f'0x{(value >> shift) & 0xffffffff:08x}'
                        for shift in range(length - 32, -1, -32))
    source = f'''/dts-v1/;
/ {{ signature {{ key-{keyname} {{
    key-name-hint = "{keyname}"; algo = "{algorithm}"; required = "conf";
    rsa,num-bits = <{bits}>;
    rsa,n0-inverse = <0x{(-pow(numbers.n, -1, 2**32)) % 2**32:08x}>;
    rsa,exponent = <{cells(numbers.e, 64)}>;
    rsa,modulus = <{cells(numbers.n, bits)}>;
    rsa,r-squared = <{cells(pow(2, 2 * bits, numbers.n), bits)}>;
}}; }}; }};
'''
    run('dtc', '-I', 'dts', '-O', 'dtb', '-o', destination, input=source, text=True)
    return fingerprint


def signing_arguments(signing, mode, work):
    name, algorithm = signing['keyname'], signing['algorithm']
    if mode == 'staging':
        keydir = REPO / signing['keydir']
        certificate = keydir / f'{name}.crt'
        extra = ['--fit-keydir', str(keydir)]
    else:
        for variable in ('AZURE_CLIENT_ID', 'AZURE_TENANT_ID'):
            if not os.environ.get(variable):
                raise ValueError(f'Missing {variable}')
        os.environ['AZURE_FEDERATED_TOKEN_FILE'] = str(work / 'azure-token')
        os.environ['XDG_CONFIG_HOME'] = str(work / 'config')
        run('python3', AKV / 'github-oidc-refresh.py')
        der = work / 'fit.der'
        run('python3', AKV / 'akv-fetch-certificate.py', '--key-id', signing['key_id'], '--output', der)
        certificate = work / 'fit.crt'
        run('openssl', 'x509', '-inform', 'DER', '-in', der, '-out', certificate)
        configdir = work / 'config/azure-keyvault-pkcs11'
        configdir.mkdir(parents=True)
        vault = signing['key_id'].split('/keys/')[0] + '/'
        (configdir / 'config.json').write_text(json.dumps({'slots': [{
            'label': name, 'key_name': name, 'vault_url': vault,
            'certificate': base64.b64encode(der.read_bytes()).decode(),
        }]}))
        extra = ['--fit-keydir', f'token={name};object={name}', '--fit-engine', 'pkcs11']
    fingerprint = trust_tree(certificate, name, algorithm, work / 'trust.dtb')
    return extra + ['--fit-keyname', name, '--fit-algorithm', algorithm], fingerprint


def package(args, config, profile, signing):
    work = args.work.resolve()
    extra, fingerprint = signing_arguments(signing, args.mode, work)
    password = os.environ.get('RECOVERY_ROOT_PASSWORD_HASH', '')
    password_file = work / 'root.hash'
    password_file.write_text(password)
    password_file.chmod(0o600)
    try:
        run('python3', REPO / 'migration/build-recovery.py', '--platform', args.platform,
            '--tezi', work / 'tezi', '--dtb', work / 'board.dtb',
            '--root-password-hash-file', password_file, '--output', work / 'output', *extra)
    finally:
        password_file.unlink(missing_ok=True)
        (work / 'azure-token').unlink(missing_ok=True)
    fit = work / 'output/recovery.itb'
    run('fit_check_sign', '-f', fit, '-k', work / 'trust.dtb', '-c', 'recovery')
    manifest_path = work / 'output/build.json'
    manifest = json.loads(manifest_path.read_text())
    manifest.update(tezi_version=config['tezi_version'], tezi_url=profile['tezi_url'],
                    kernel_revision=config['kernel_revision'], bsp_revision=config['bsp_revision'],
                    ci4rail_bsp_revision=config['ci4rail_bsp_revision'],
                    board_patch_sha256=build.digest(work / 'ci4rail-bsp' / BSP_PATCH), signing_mode=args.mode,
                    fit_spki_sha256=fingerprint, signature_verified=True,
                    git_commit=os.environ.get('GITHUB_SHA'), run_id=os.environ.get('GITHUB_RUN_ID'),
                    run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),
                    version=os.environ.get('RECOVERY_VERSION'))
    build.write_json(manifest_path, manifest)
    write_release_checksums(work / 'output')


def write_release_checksums(output):
    # The local builder also emits boot scripts; CI publishes only these files.
    (output / 'SHA256SUMS').write_text(''.join(
        f'{build.digest(output / name)}  {name}\n' for name in ('build.json', 'recovery.itb')))


def upload(args):
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', endpoint_url='https://minio.ci4rail.com',
                          aws_access_key_id=os.environ['MINIO_ACCESS_KEY'],
                          aws_secret_access_key=os.environ['MINIO_SECRET_KEY'],
                          region_name='us-east-1',
                          config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}))
    bucket = os.environ['MINIO_BUCKET']
    # Fail closed if policy cannot be inspected. CI never changes bucket policy.
    from botocore.exceptions import ClientError
    try:
        policy = json.loads(client.get_bucket_policy(Bucket=bucket)['Policy'])
    except ClientError as error:
        if error.response['Error']['Code'] != 'NoSuchBucketPolicy':
            raise
        policy = {}
    assert_private_policy(policy)
    prefix = object_prefix(os.environ['MINIO_PREFIX'], args.platform, args.mode,
                           os.environ['RECOVERY_VERSION'], os.environ['GITHUB_SHA'],
                           os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT'])
    for name in ('recovery.itb', 'build.json', 'SHA256SUMS'):
        path = args.work / 'output' / name
        client.upload_file(str(path), bucket, f'{prefix}/{name}')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as target:
        target.write(f'object_uri=s3://{bucket}/{prefix}/recovery.itb\n')
        target.write(f'sha256={build.digest(args.work / "output/recovery.itb")}\n')


def assert_private_policy(policy):
    statements = policy.get('Statement', [])
    if isinstance(statements, dict):
        statements = [statements]
    for statement in statements:
        principal = statement.get('Principal')
        values = principal.values() if isinstance(principal, dict) else [principal]
        wildcard = any(value == '*' or isinstance(value, list) and '*' in value for value in values)
        if statement.get('Effect') == 'Allow' and ('NotPrincipal' in statement or wildcard):
            raise ValueError('Recovery destination bucket permits anonymous access; refusing upload')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate', 'prepare', 'package', 'upload'))
    parser.add_argument('--platform', required=True, choices=('cpu01', 'cpu01plus'))
    parser.add_argument('--mode', required=True, choices=('staging', 'production'))
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    config, profile, signing = settings(args.platform, args.mode)
    if args.action == 'validate':
        validate_environment(args.mode)
    elif args.action == 'prepare':
        args.work.mkdir(parents=True, exist_ok=True)
        prepare(config, profile, args.work.resolve())
    elif args.action == 'package':
        package(args, config, profile, signing)
    elif args.action == 'upload':
        upload(args)


if __name__ == '__main__':
    main()
