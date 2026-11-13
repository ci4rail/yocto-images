#!/usr/bin/env python3
"""Package a signed testfarm USB recovery bundle; does not contain an OS payload."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'migration/ci'))
import recovery


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


# Toradex U-Boot advertises Fastboot as 0x4000 + config-block product ID
# (0x4000 without a config block): Verdin iMX8MM 0055, Verdin iMX8MP 0058.
FASTBOOT_PIDS = {'cpu01': ('0x4000', '0x4037'), 'cpu01plus': ('0x4000', '0x403a')}


def uuu_script(platform):
    if platform == 'cpu01':
        rom = '''SDP: boot -f ../imx-boot-recovery
CFG: SDPV: -chip SPL1 -vid 0x1b67 -pid 0x4fff
SDPV: delay 1000
SDPV: write -f ../imx-boot-recovery -skipspl
SDPV: jump
'''
    elif platform == 'cpu01plus':
        rom = 'SDPS: boot -f ../imx-boot-recovery\n'
    else:
        raise ValueError('Unsupported platform')
    fastboot = ''.join(f'CFG: FB: -vid 0x1b67 -pid {pid}\n' for pid in FASTBOOT_PIDS[platform])
    return ('uuu_version 1.4.127\n\n' + rom + fastboot +
            'FB: download -f ../recovery.itb\n'
            'FB: continue\nFB: done\n')


def validate_inputs(bootloader, runtime, platform, mode):
    _, _, signing = recovery.settings(platform, mode)
    manifest = json.loads((runtime / 'build.json').read_text())
    if manifest.get('platform') != platform or manifest.get('signing_mode') != mode:
        raise ValueError('Recovery runtime platform/signing mode mismatch')
    if manifest.get('signature_verified') is not True:
        raise ValueError('Recovery runtime has not passed signature verification')
    fit = runtime / 'recovery.itb'
    if not 0 < fit.stat().st_size <= 0x10000000:
        raise ValueError('Recovery FIT exceeds the fixed download buffer')
    config = (bootloader / 'u-boot.config').read_text().splitlines()
    required = ('CONFIG_IMX_HAB=y', 'CONFIG_FIT_SIGNATURE=y',
                'CONFIG_TDX_SECBOOT_HARDENING=y', 'CONFIG_TDX_FB_PROTECTION=y',
                'CONFIG_TDX_FB_PROTECTION_ALLOW_CMD=y',
                'CONFIG_FASTBOOT_BUF_ADDR=0x60000000', 'CONFIG_FASTBOOT_BUF_SIZE=0x10000000',
                'CONFIG_TARGET_VERDIN_IMX8MM=y' if platform == 'cpu01' else
                'CONFIG_TARGET_VERDIN_IMX8MP=y')
    if not all(value in config for value in required):
        raise ValueError('Unexpected recovery bootloader configuration')
    if any(value in config for value in ('CONFIG_FASTBOOT_FLASH=y', 'CONFIG_FASTBOOT_UUU_SUPPORT=y')):
        raise ValueError('Unrestricted Fastboot is enabled')
    # These are build-consistency checks, not a substitute for ROM HAB validation.
    flash = (bootloader / 'imx-boot-recovery').read_bytes()
    for name in ('csf-spl.bin', 'csf-fit.bin'):
        csf = (bootloader / name).read_bytes()
        if len(csf) < 4 or csf[0] != 0xD4 or csf not in flash:
            raise ValueError('Missing HAB CSF in bootloader: ' + name)
    if (bootloader / 'srk-fuse.bin').stat().st_size != 32:
        raise ValueError('Expected 256-bit HAB SRK fuse digest')
    # fit_check_sign must see a mandatory configuration key, not an empty trust tree.
    required = subprocess.check_output([
        'fdtget', '-t', 's', str(bootloader / 'u-boot.dtb'),
        '/signature/key-' + signing['keyname'], 'required'], text=True).strip()
    if required != 'conf':
        raise ValueError('Bootloader FIT configuration key is not mandatory')
    # Verify against the actual public-key DTB exported by the bootloader build.
    subprocess.run(['fit_check_sign', '-f', str(fit), '-k',
                    str(bootloader / 'u-boot.dtb'), '-c', 'recovery'], check=True)
    return manifest


def package(args):
    manifest = validate_inputs(args.bootloader, args.runtime, args.platform, args.mode)
    if args.output.exists():
        raise ValueError('Output already exists')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as temp:
        bundle = Path(temp) / 'bundle'
        bundle.mkdir()
        for name in ('imx-boot-recovery', 'u-boot.dtb', 'u-boot.config', 'srk-fuse.bin'):
            shutil.copyfile(args.bootloader / name, bundle / name)
        shutil.copyfile(args.runtime / 'recovery.itb', bundle / 'recovery.itb')
        shutil.copyfile(args.runtime / 'build.json', bundle / 'runtime-build.json')
        (bundle / 'recovery').mkdir()
        (bundle / 'recovery/uuu.auto').write_text(uuu_script(args.platform))
        manifest = {
            'schema_version': 1, 'purpose': 'testfarm-usb-recovery',
            'platform': args.platform, 'signing_mode': args.mode,
            'version': manifest['version'], 'git_commit': os.environ['GITHUB_SHA'],
            'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
            'fit_spki_sha256': manifest['fit_spki_sha256'],
            'hab_srk_fuse_hex': (bundle / 'srk-fuse.bin').read_bytes().hex(),
            'fit_verified_with_bootloader_dtb': True,
            'hab_verification': 'CSF presence checked; ROM authentication requires target qualification',
            'hardware_qualified': False,
            'bootloader_layers': json.loads(args.revisions.read_text()),
            'uuu_min_version': '1.4.127',
            'files': {str(p.relative_to(bundle)): digest(p)
                      for p in sorted(bundle.rglob('*')) if p.is_file()},
        }
        (bundle / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (bundle / 'SHA256SUMS').write_text(''.join(
            f'{digest(p)}  {p.relative_to(bundle)}\n'
            for p in sorted(bundle.rglob('*')) if p.is_file()))
        archive = Path(temp) / 'recovery-bundle.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for path in sorted(bundle.rglob('*')):
                if path.is_file():
                    tar.add(path, arcname=str(path.relative_to(bundle)), recursive=False)
        shutil.move(archive, args.output)
    args.output.with_suffix(args.output.suffix + '.sha256').write_text(
        f'{digest(args.output)}  {args.output.name}\n')


def upload(args):
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
    client = boto3.client('s3', endpoint_url='https://minio.ci4rail.com',
                          aws_access_key_id=os.environ['MINIO_ACCESS_KEY'],
                          aws_secret_access_key=os.environ['MINIO_SECRET_KEY'],
                          region_name='us-east-1',
                          config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}))
    bucket = os.environ['MINIO_BUCKET']
    try:
        policy = json.loads(client.get_bucket_policy(Bucket=bucket)['Policy'])
    except ClientError as error:
        if error.response['Error']['Code'] != 'NoSuchBucketPolicy':
            raise
        policy = {}
    recovery.assert_private_policy(policy)
    prefix = recovery.object_prefix('testfarm-recovery', args.platform, args.mode,
                                     os.environ['RECOVERY_VERSION'], os.environ['GITHUB_RUN_ID'],
                                     os.environ['GITHUB_RUN_ATTEMPT'])
    for path in (args.output, args.output.with_suffix(args.output.suffix + '.sha256')):
        client.upload_file(str(path), bucket, f'{prefix}/{path.name}')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'object_uri=s3://{bucket}/{prefix}/{args.output.name}\n')
        output.write(f'sha256={digest(args.output)}\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('package', 'upload'))
    parser.add_argument('--platform', choices=('cpu01', 'cpu01plus'), required=True)
    parser.add_argument('--mode', choices=('staging', 'production'), required=True)
    parser.add_argument('--bootloader', type=Path)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--revisions', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'package':
        if not all((args.bootloader, args.runtime, args.revisions)):
            parser.error('package requires --bootloader, --runtime and --revisions')
        package(args)
    else:
        upload(args)


if __name__ == '__main__':
    main()
