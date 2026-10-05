#!/usr/bin/env python3
"""Package a platform-specific SD recovery installer from TEZI and a released Mender image.

No target devices are accessed. Input archives must come from trusted builds.
The deliberately narrow payload format is this repository's four-partition
mender_tezi format; this is not a general-purpose TEZI image converter.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent
MIB = 1024 * 1024


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def output(*args):
    return run(*args, stdout=subprocess.PIPE, text=True).stdout.strip()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(MIB), b''):
            h.update(data)
    return h.hexdigest()


def safe_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_+.,=-]+', name):
        raise ValueError(f'Expected a plain local filename, got {name!r}')
    if name in ('.', '..'):
        raise ValueError('Invalid filename')
    return name


def extract_payload(archive, dest):
    """Reject links, traversal, duplicates and special files before extracting."""
    with tarfile.open(archive, 'r:*') as tf:
        members = tf.getmembers()
        seen = set()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts:
                raise ValueError('Unsafe archive path')
            if not (member.isfile() or member.isdir()) or path in seen:
                raise ValueError('Archive contains a link, special file or duplicate')
            seen.add(path)
        for member in members:
            target = dest.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(member) as source, target.open('wb') as sink:
                    shutil.copyfileobj(source, sink)
                target.chmod(0o644)
    manifests = list(dest.rglob('image.json'))
    if len(manifests) != 1:
        raise ValueError('Payload must contain exactly one image.json')
    return manifests[0].parent


def validate_payload(folder):
    """Allow only our current BOOTFIT/rootfs A/rootfs B/data plus boot0 layout."""
    manifest = json.loads((folder / 'image.json').read_text())
    allowed = {'config_format', 'autoinstall', 'name', 'description', 'version',
               'release_date', 'u_boot_env', 'prepare_script', 'wrapup_script',
               'error_script', 'marketing', 'icon', 'supported_product_ids', 'blockdevs'}
    if set(manifest) - allowed:
        raise ValueError('Unsupported top-level TEZI properties')
    if str(manifest.get('config_format')) != '4':
        raise ValueError('Expected TEZI config_format 4')
    pids = manifest.get('supported_product_ids', [])
    if not isinstance(pids, list) or not pids or any(
            not isinstance(pid, str) or not pid.strip() for pid in pids):
        raise ValueError('Payload product IDs must be a nonempty list of nonempty strings')
    # Preserve release compatibility metadata; TEZI checks it on the device.
    blocks = manifest['blockdevs']
    if len(blocks) != 2:
        raise ValueError('Expected eMMC and eMMC boot0 only')
    disk, boot = blocks
    if set(disk) != {'name', 'partitions'} or set(boot) != {'name', 'erase', 'content'}:
        raise ValueError('Unexpected block device properties')
    if not re.fullmatch(r'mmcblk[0-9]+', disk['name']) or boot['name'] != disk['name'] + 'boot0':
        raise ValueError('Unexpected target devices')
    parts = disk['partitions']
    if len(parts) != 4:
        raise ValueError('Expected four partitions')
    refs = set()
    for index, part in enumerate(parts):
        if set(part) - {'partition_size_nominal', 'partition_type', 'want_maximised', 'offset_in_sectors', 'content'}:
            raise ValueError('Unsupported partition property')
        size = int(part['partition_size_nominal'])
        if size <= 0 or part['partition_type'] != '83' or part.get('want_maximised') != (index == 3):
            raise ValueError('Unexpected partition size, type or maximization')
        if index > 0 and 'offset_in_sectors' in part:
            raise ValueError('Only BOOTFIT may specify an offset')
        content = part['content']
        if index == 0:
            if set(content) - {'label', 'filesystem_type', 'mkfs_options', 'filename', 'uncompressed_size'}:
                raise ValueError('Unsupported BOOTFIT content')
            if content.get('label') != 'BOOTFIT' or content['filesystem_type'] != 'ext4' or content.get('mkfs_options', ''):
                raise ValueError('Expected plain ext4 BOOTFIT')
            refs.add(safe_name(content['filename']))
            offset = int(part['offset_in_sectors'])
            if offset < 8192:
                raise ValueError('BOOTFIT must leave space for the U-Boot environment')
        else:
            if set(content) != {'filesystem_type', 'rawfiles', 'uncompressed_size'} or content['filesystem_type'] != 'raw':
                raise ValueError('Expected raw rootfs/data images')
            raw = content['rawfiles']
            if len(raw) != 1 or set(raw[0]) != {'filename'}:
                raise ValueError('Unsupported raw partition write')
            filename = safe_name(raw[0]['filename'])
            refs.add(filename)
            if (folder / filename).stat().st_size > size * MIB:
                raise ValueError('Raw image is larger than its partition')
    if parts[1]['content'] != parts[2]['content']:
        raise ValueError('Expected identical initial rootfs A and B')
    if boot['erase'] is not True or set(boot['content']) != {'filesystem_type', 'rawfiles'}:
        raise ValueError('Unexpected boot0 content')
    if boot['content']['filesystem_type'] != 'raw':
        raise ValueError('Expected raw bootloader')
    rawfiles = boot['content']['rawfiles']
    if not 1 <= len(rawfiles) <= 2:
        raise ValueError('Expected one or two bootloader components')
    for raw in rawfiles:
        if set(raw) != {'filename', 'dd_options'} or not re.fullmatch(r'seek=[0-9]+', raw['dd_options']):
            raise ValueError('Unsupported bootloader write options')
        refs.add(safe_name(raw['filename']))
    env = safe_name(manifest['u_boot_env'])
    refs.add(env)
    # Never carry automatic fuse provisioning into the installed environment.
    # Match OTP as a distinct identifier; "bootpart" contains those letters.
    if re.search(r'fuse|srk|(?:^|[^a-z0-9])otp(?:[^a-z0-9]|$)',
                 (folder / env).read_text(), re.I):
        raise ValueError('Fuse-related U-Boot environment requires manual review; refusing')
    for name in refs:
        if not (folder / name).is_file() or (folder / name).is_symlink():
            raise ValueError(f'Missing regular payload file: {name}')
    disk['name'], boot['name'] = 'emmc', 'emmc-boot0'
    manifest['autoinstall'] = False  # Installation requires selection in the TEZI UI
    # Discard vendor hooks, including provisioning and automatic data handling.
    for key in ('prepare_script', 'wrapup_script', 'error_script', 'marketing', 'icon'):
        manifest.pop(key, None)
    return manifest, refs


def fit_component(fit, kind, work):
    nodes = output('fdtget', '-l', fit, '/images').splitlines()
    matches = [n for n in nodes if output('fdtget', fit, f'/images/{n}', 'type') == kind]
    if len(matches) != 1:
        raise ValueError(f'Installer FIT must have exactly one {kind}')
    node = matches[0]
    target = work / kind
    run('dumpimage', '-T', 'flat_dt', '-p', nodes.index(node), '-o', target, fit,
        stdout=subprocess.DEVNULL)
    compression = output('fdtget', fit, f'/images/{node}', 'compression')
    return target, node, compression


def recovery_bootargs(console):
    # Must match moducop-recovery.c, independently of the FIT size limit.
    return f'console={console},115200 root=/dev/ram0 rootfstype=squashfs ro ramdisk_size=393216'


def prepare_recovery_dtb(dtb, console):
    run('fdtput', '-p', '-t', 's', dtb,
        '/chosen/toradex,secure-boot', 'required-bootargs', recovery_bootargs(console))


def make_fit(kernel, ramdisk, dtb, load, compression, dest,
             keydir=None, keyname='dev', engine=None, algorithm='sha256,rsa2048'):
    # ramdisk has no fixed load address: U-Boot relocates it, avoiding overlap
    # between the RAM root filesystem and the original FIT buffer.
    if not re.fullmatch(r'[A-Za-z0-9_+-]+', keyname):
        raise ValueError('Invalid FIT key name')
    if algorithm not in ('sha256,rsa2048', 'sha256,rsa3072', 'sha256,rsa4096'):
        raise ValueError('Unsupported FIT signing algorithm')
    signature = ''
    if keydir:
        signature = f'''signature {{
    algo = "{algorithm}"; key-name-hint = "{keyname}";
    sign-images = "kernel", "fdt", "ramdisk";
   }};'''
    its = dest.parent / 'migration.its'
    its.write_text(f'''/dts-v1/;
/ {{
 description = "Ci4Rail RAM migration installer";
 #address-cells = <1>;
 images {{
  kernel {{
   data = /incbin/("{kernel}"); type = "kernel"; arch = "arm64";
   os = "linux"; compression = "{compression}";
   load = <0x{load:x}>; entry = <0x{load:x}>;
   hash {{ algo = "sha256"; }};
  }};
  fdt {{
   data = /incbin/("{dtb}"); type = "flat_dt"; arch = "arm64";
   compression = "none"; hash {{ algo = "sha256"; }};
  }};
  ramdisk {{
   data = /incbin/("{ramdisk}"); type = "ramdisk"; arch = "arm64";
   os = "linux"; compression = "none"; hash {{ algo = "sha256"; }};
  }};
 }};
 configurations {{
  default = "recovery";
  recovery {{ kernel = "kernel"; fdt = "fdt"; ramdisk = "ramdisk";
   {signature}
  }};
 }};
}};
''')
    signing = ['-k', keydir] if keydir else []
    if engine:
        signing += ['-N', engine]
    run('mkimage', '-f', its, *signing, dest, stdout=subprocess.DEVNULL)
    if keydir and not output('fdtget', '-t', 'bx', dest,
                             '/configurations/recovery/signature', 'value'):
        raise ValueError('FIT signature missing')
    its.unlink()


def boot_scripts(dest, console):
    boot = f'''# The recovery FIT is loaded before Linux starts; the payload stays on SD.
# No saveenv, eMMC writes, or fuse commands are performed here.
setenv bootargs {recovery_bootargs(console)}
setenv fdt_high
setenv initrd_high
setenv bootm_low 0x40000000
setenv bootm_size 0x58000000
if load ${{devtype}} ${{devnum}}:${{distro_bootpart}} 0x60000000 ${{prefix}}recovery.itb; then
    bootm 0x60000000#recovery
fi
'''
    (dest / 'sd.cmd').write_text(boot.replace(
        'load ${devtype} ${devnum}:${distro_bootpart} 0x60000000 ${prefix}recovery.itb',
        'load mmc 1:1 0x60000000 /recovery.itb'))
    (dest / 'boot.cmd').write_text(boot)
    run('mkimage', '-A', 'arm64', '-O', 'linux', '-T', 'script', '-C', 'none',
        '-n', 'Ci4Rail migration', '-d', dest / 'boot.cmd', dest / 'boot.scr',
        stdout=subprocess.DEVNULL)
    (dest / 'tftp.cmd').write_text(boot.replace(
        'load ${devtype} ${devnum}:${distro_bootpart} 0x60000000 ${prefix}recovery.itb',
        'tftpboot 0x60000000 recovery.itb'))
    run('mkimage', '-A', 'arm64', '-O', 'linux', '-T', 'script', '-C', 'none',
        '-n', 'Ci4Rail migration TFTP', '-d', dest / 'tftp.cmd', dest / 'tftp.scr',
        stdout=subprocess.DEVNULL)


def load_profile(platform):
    return json.loads((ROOT.parent / f'{platform}-migration-image/profile.json').read_text())


def require_tools(*names):
    for name in names:
        if not shutil.which(name):
            raise ValueError(f'Missing host tool: {name}')


def installer_metadata(tezi):
    metadata = json.loads((tezi / 'image.json').read_text())
    if not metadata.get('isinstaller'):
        raise ValueError('--tezi must point to the TEZI runtime, not the OS image')
    return metadata


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def write_checksums(folder):
    (folder / 'SHA256SUMS').write_text(''.join(
        f'{digest(p)}  {p.relative_to(folder)}\n'
        for p in sorted(folder.rglob('*'))
        if p.is_file() and p != folder / 'SHA256SUMS'))


def publish_output(source, destination):
    if destination.exists():
        raise ValueError('Output directory already exists')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(source, destination)


def package_payload(args, destination):
    """Convert a trusted release archive into a locally discoverable TEZI folder."""
    with tempfile.TemporaryDirectory(prefix='migration-payload-') as temp:
        extracted = Path(temp)
        source = extract_payload(args.payload, extracted)
        manifest, refs = validate_payload(source)
        destination.mkdir()
        for name in sorted(refs):
            shutil.copyfile(source / name, destination / name)
        write_json(destination / 'image.json', manifest)
        write_checksums(destination)


def validate_recovery_inputs(args, profile):
    if not args.allow_unsigned and not args.fit_keydir:
        raise ValueError('Signed recovery requires --fit-keydir; '
                         'use --allow-unsigned only for open development devices')
    if args.fit_engine and not args.fit_keydir:
        raise ValueError('--fit-engine requires --fit-keydir')
    require_tools('fdtput', 'dumpimage', 'mkimage', 'fdtget', 'dtc',
                  'unsquashfs', 'mksquashfs', 'aarch64-linux-gnu-gcc')
    tezi = args.tezi.resolve()
    dtb = args.dtb.resolve()
    if profile['soc'] not in output('fdtget', dtb, '/', 'compatible').split():
        raise ValueError('Device tree does not match platform')
    if 'ModuCop' not in output('fdtget', dtb, '/', 'model'):
        raise ValueError('A ModuCop installer device tree is required')
    family = profile['soc'].replace('fsl,', 'verdin-')
    if family not in output('fdtget', tezi / 'tezi.itb', '/', 'description'):
        raise ValueError('TEZI runtime belongs to a different SoC')
    installer_metadata(tezi)
    return tezi, dtb


def unpack_installer(tezi, work):
    """Extract the kernel and RAM filesystem, checking the recovery load contract."""
    kernel, node, compression = fit_component(tezi / 'tezi.itb', 'kernel', work)
    if compression not in ('gzip', 'none'):
        raise ValueError('Unsupported installer kernel compression')
    load = int(output('fdtget', '-t', 'x', tezi / 'tezi.itb', f'/images/{node}', 'load'), 16)
    if not 0x40000000 <= load < 0x50000000:
        raise ValueError('Unexpected installer kernel load address')
    ramdisk, _, ramcompression = fit_component(tezi / 'tezi.itb', 'ramdisk', work)
    if ramcompression != 'none':
        raise ValueError('Expected an uncompressed FIT component containing squashfs')
    rootfs = work / 'rootfs'
    run('unsquashfs', '-no-progress', '-d', rootfs, ramdisk, stdout=subprocess.DEVNULL)
    if not (rootfs / 'usr/bin/tezictl').is_file() or not (rootfs / 'usr/bin/weston').is_file():
        raise ValueError('TEZI runtime needs tezictl and Weston for the interactive UI')
    if not (rootfs / 'bin/tar.tar').is_file():
        raise ValueError('TEZI runtime needs GNU tar for metadata-preserving backup')
    return rootfs, kernel, load, compression


def read_root_password_hash(path):
    """Accept SHA-512 crypt only; never include credentials in error messages."""
    value = path.read_text().rstrip('\n')
    if not re.fullmatch(r'\$6\$(?:rounds=[1-9][0-9]{3,8}\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}', value):
        raise ValueError('Root password hash must be a single SHA-512 crypt hash')
    return value


def configure_console_auth(rootfs, password_hash):
    """Replace TEZI's unauthenticated shell, refusing unknown init layouts."""
    init = rootfs / 'sbin/init'
    startup = init.read_text()
    shell = '\tsetsid cttyhack sh\n'
    if startup.count(shell) != 1:
        raise ValueError('Unsupported TEZI init: expected one serial shell launch')
    # Use the concrete binary: the vendor sulogin symlink is absolute.
    if not (rootfs / 'sbin/sulogin.util-linux').is_file():
        raise ValueError('TEZI runtime needs util-linux sulogin')
    shadow = rootfs / 'etc/shadow'
    rows = shadow.read_text().splitlines()
    if sum(row.startswith('root:') for row in rows) != 1:
        raise ValueError('Expected one root shadow entry')
    rows = [f'root:{password_hash}:0:0:99999:7:::' if row.startswith('root:') else row
            for row in rows]
    shadow.chmod(0o600)
    shadow.write_text('\n'.join(rows) + '\n')
    # No --force: authentication errors and EOF must never open a shell.
    init.write_text(startup.replace(shell, '\tsetsid cttyhack /sbin/sulogin.util-linux\n'))


def configure_runtime(rootfs, profile):
    """Install manual data tools and minimal shell/network/TEZI startup."""
    embedded = rootfs / 'migration'
    embedded.mkdir(exist_ok=True)
    for name in ('common.sh', 'backup-data.sh', 'restore-data.sh', 'mount-sd.sh', 'start-tezi.sh'):
        shutil.copyfile(ROOT / 'runtime' / name, embedded / name)
        (embedded / name).chmod(0o755)
    run('aarch64-linux-gnu-gcc', '-static', '-Os', '-Wall', '-Wextra', '-Werror', '-s',
        ROOT / 'runtime/split-backup.c', '-o', embedded / 'split-backup')
    (embedded / 'profile.conf').write_text(f"SOC='{profile['soc']}'\n")
    shutil.copyfile(ROOT / 'runtime/rc.local', rootfs / 'etc/rc.local')
    (rootfs / 'etc/rc.local').chmod(0o755)
    # TEZI uses fw_setenv to install the payload's u_boot_env. Its stock
    # single-copy layout does not match this project's redundant Mender layout.
    shutil.copyfile(ROOT / 'runtime/fw_env.config', rootfs / 'etc/fw_env.config')
    (rootfs / 'etc/network/interfaces').write_text(
        'auto lo\niface lo inet loopback\n\n'
        'auto eth0\niface eth0 inet manual\n'
        '    up ip link set eth0 up\n'
        '    up udhcpc -b -i eth0 -p /run/udhcpc.eth0.pid\n')
    # Mount only SD explicitly, under TEZI's local media search directory.
    for base in ('etc/udev/rules.d', 'lib/udev/rules.d', 'usr/lib/udev/rules.d'):
        for pattern in ('*automount*', '*ifplugd*'):
            for rule in (rootfs / base).glob(pattern):
                rule.unlink()
    write_json(rootfs / 'etc/tezi_config.json', {
        'config_format': 1, 'image_lists': [],
        'show_default_feed': False, 'show_3rdparty_feed': False})


def package_recovery(args, destination):
    """Build recovery.itb without reading or embedding an OS payload."""
    profile = load_profile(args.platform)
    password_hash = read_root_password_hash(args.root_password_hash_file)
    tezi, dtb = validate_recovery_inputs(args, profile)
    with tempfile.TemporaryDirectory(prefix='migration-recovery-') as temp:
        work = Path(temp).resolve()
        rootfs, kernel, load, compression = unpack_installer(tezi, work)
        configure_runtime(rootfs, profile)
        configure_console_auth(rootfs, password_hash)
        squashfs = work / 'migration.squashfs'
        run('mksquashfs', rootfs, squashfs, '-noappend', '-all-root', '-comp', 'gzip',
            '-no-progress', '-processors', '2', stdout=subprocess.DEVNULL)
        destination.mkdir()
        shutil.copyfile(dtb, work / 'board.dtb')
        prepare_recovery_dtb(work / 'board.dtb', profile['console'])
        make_fit(kernel, squashfs, work / 'board.dtb', load, compression,
                 destination / 'recovery.itb', args.fit_keydir, args.fit_keyname, args.fit_engine,
                 args.fit_algorithm)
        size = (destination / 'recovery.itb').stat().st_size
        if size > profile['maximum_fit_mib'] * MIB:
            raise ValueError(f"Recovery FIT exceeds {profile['maximum_fit_mib']} MiB loader limit")
        boot_scripts(destination, profile['console'])
        write_json(destination / 'build.json', {
            'platform': args.platform, 'profile': profile,
            'tezi_fit_sha256': digest(tezi / 'tezi.itb'), 'dtb_sha256': digest(dtb),
            'fw_env_config_sha256': digest(ROOT / 'runtime/fw_env.config'), 'fit_size_bytes': size,
            'fit_signed': bool(args.fit_keydir),
            'fit_keyname': args.fit_keyname if args.fit_keydir else None,
            'fit_algorithm': args.fit_algorithm if args.fit_keydir else None,
            'fuse_programming': False, 'hardware_validated': False,
        })
        write_checksums(destination)


def build(args, mode='bundle'):
    """Stage outputs before publishing; retain the original combined build entry point."""
    if args.output.exists():
        raise ValueError('Output directory already exists')
    with tempfile.TemporaryDirectory(prefix='migration-output-') as temp:
        destination = Path(temp) / 'result'
        if mode == 'payload':
            package_payload(args, destination)
        else:
            package_recovery(args, destination)
            if mode == 'bundle':
                package_payload(args, destination / 'image')
                write_checksums(destination)
        publish_output(destination, args.output)
    print(f'Created {args.output}. Hardware validation still required.')


def argument_parser(mode):
    parser = argparse.ArgumentParser(description={
        'bundle': 'Build recovery and payload together.',
        'recovery': 'Build recovery.itb and boot scripts independently of the payload.',
        'payload': 'Build a manual-install TEZI payload folder for SD discovery.',
    }[mode])
    parser.add_argument('--output', type=Path, required=True, help='New output directory')
    if mode != 'recovery':
        parser.add_argument('--payload', type=Path, required=True, help='Trusted .mender_tezi.tar')
    if mode != 'payload':
        add_recovery_arguments(parser)
    return parser


def add_recovery_arguments(parser):
    parser.add_argument('--tezi', type=Path, required=True, help='Extracted platform TEZI runtime')
    parser.add_argument('--platform', choices=('cpu01', 'cpu01plus'), required=True)
    parser.add_argument('--dtb', type=Path, required=True)
    parser.add_argument('--root-password-hash-file', type=Path, required=True,
                        help='File containing a SHA-512 crypt hash for the recovery root password')
    parser.add_argument('--fit-keydir', help='mkimage signing key directory (production: CI only)')
    parser.add_argument('--fit-keyname', default='dev')
    parser.add_argument('--fit-engine', help='OpenSSL signing engine, e.g. pkcs11')
    parser.add_argument('--fit-algorithm', default='sha256,rsa2048',
                        choices=('sha256,rsa2048', 'sha256,rsa3072', 'sha256,rsa4096'))
    parser.add_argument('--allow-unsigned', action='store_true', help='Open development devices only')


def main(mode='bundle'):
    parser = argument_parser(mode)
    args = parser.parse_args()
    try:
        build(args, mode)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Migration build failed: {error}\n')


if __name__ == '__main__':
    main()
