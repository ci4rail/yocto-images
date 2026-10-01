#!/usr/bin/env python3
"""Package a platform-specific RAM installer from TEZI and a released Mender image.

No target devices are accessed. Input archives must come from trusted builds.
The deliberately narrow payload format is this repository's four-partition
mender_tezi format; this is not a general-purpose TEZI image converter.
"""
import argparse
import hashlib
import json
import os
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


def validate_payload(folder, installer_product_ids):
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
    if not pids or not set(pids).issubset(set(installer_product_ids)):
        raise ValueError('Payload product IDs do not match this TEZI installer')
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
    minimum_mib = 8
    for index, part in enumerate(parts):
        if set(part) - {'partition_size_nominal', 'partition_type', 'want_maximised', 'offset_in_sectors', 'content'}:
            raise ValueError('Unsupported partition property')
        size = int(part['partition_size_nominal'])
        if size <= 0 or part['partition_type'] != '83' or part.get('want_maximised') != (index == 3):
            raise ValueError('Unexpected partition size, type or maximization')
        if index > 0 and 'offset_in_sectors' in part:
            raise ValueError('Only BOOTFIT may specify an offset')
        minimum_mib += size + 4  # room for per-partition alignment
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
            minimum_mib += (offset * 512 + MIB - 1) // MIB
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
    manifest['autoinstall'] = True  # TEZI autoinstalls only after discovering our local feed
    # These hooks replace the standard no-op preparation and data resize hook.
    # TEZI already maximizes partition 4. The data filesystem remains at its
    # image size, so preparation checks its free space before erasing.
    for key in ('prepare_script', 'wrapup_script', 'error_script', 'marketing', 'icon'):
        manifest.pop(key, None)
    manifest.update(prepare_script='prepare.sh', wrapup_script='wrapup.sh', error_script='error.sh')
    return manifest, refs, minimum_mib


def validate_env_config(path):
    # The current Mender integration uses two 8 KiB copies at the end of boot0.
    # Check an exported target config instead of assuming the stock TEZI layout.
    rows = [line.split() for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith('#')]
    if len(rows) != 2 or any(len(row) != 3 for row in rows):
        raise ValueError('Expected two three-column eMMC environment entries')
    if rows[0][0] != rows[1][0] or not re.fullmatch(r'/dev/mmcblk[0-9]+boot0', rows[0][0]):
        raise ValueError('Expected redundant environments in the same eMMC boot0')
    entries = {(int(row[1], 0), int(row[2], 0)) for row in rows}
    if entries != {(-0x2200, 0x2000), (-0x4200, 0x2000)}:
        raise ValueError('Unsupported target environment offsets or sizes')
    return '/dev/emmc-boot0 -0x2200 0x2000\n/dev/emmc-boot0 -0x4200 0x2000\n'


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


def make_fit(kernel, ramdisk, dtb, load, compression, dest):
    # ramdisk has no fixed load address: U-Boot relocates it, avoiding overlap
    # between a large embedded payload and the original FIT buffer.
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
  default = "migration";
  migration {{ kernel = "kernel"; fdt = "fdt"; ramdisk = "ramdisk"; }};
 }};
}};
''')
    run('mkimage', '-f', its, dest, stdout=subprocess.DEVNULL)
    its.unlink()


def boot_scripts(dest, maximum_mib, console):
    boot = f'''# The complete FIT (including payload) is loaded before Linux starts.
# No saveenv, eMMC writes, or fuse commands are performed here.
setenv bootargs console={console},115200 root=/dev/ram0 rootfstype=squashfs ro ramdisk_size={maximum_mib * 1024}
setenv fdt_high
setenv initrd_high
setenv bootm_low 0x40000000
setenv bootm_size 0x{(maximum_mib * 3 + 256) * MIB:x}
if load ${{devtype}} ${{devnum}}:${{distro_bootpart}} 0x60000000 ${{prefix}}migration.itb; then
    bootm 0x60000000#migration
fi
'''
    (dest / 'boot.cmd').write_text(boot)
    run('mkimage', '-A', 'arm64', '-O', 'linux', '-T', 'script', '-C', 'none',
        '-n', 'Ci4Rail migration', '-d', dest / 'boot.cmd', dest / 'boot.scr',
        stdout=subprocess.DEVNULL)
    (dest / 'tftp.cmd').write_text(boot.replace(
        'load ${devtype} ${devnum}:${distro_bootpart} 0x60000000 ${prefix}migration.itb',
        'tftpboot 0x60000000 migration.itb'))
    run('mkimage', '-A', 'arm64', '-O', 'linux', '-T', 'script', '-C', 'none',
        '-n', 'Ci4Rail migration TFTP', '-d', dest / 'tftp.cmd', dest / 'tftp.scr',
        stdout=subprocess.DEVNULL)


def build(args):
    profile = json.loads((ROOT.parent / f'{args.platform}-migration-image/profile.json').read_text())
    for tool in ('dumpimage', 'mkimage', 'fdtget', 'dtc', 'unsquashfs', 'mksquashfs',
                 'dumpe2fs', 'aarch64-linux-gnu-gcc'):
        if not shutil.which(tool):
            raise ValueError(f'Missing host tool: {tool}')
    env_config = validate_env_config(args.fw_env_config)
    tezi = args.tezi.resolve()
    dtb = args.dtb.resolve()
    if profile['soc'] not in output('fdtget', dtb, '/', 'compatible').split():
        raise ValueError('Device tree does not match platform')
    if 'ModuCop' not in output('fdtget', dtb, '/', 'model'):
        raise ValueError('A ModuCop installer device tree is required')
    family = profile['soc'].replace('fsl,', 'verdin-')
    if family not in output('fdtget', tezi / 'tezi.itb', '/', 'description'):
        raise ValueError('TEZI runtime belongs to a different SoC')
    metadata = json.loads((tezi / 'image.json').read_text())
    if not metadata.get('isinstaller'):
        raise ValueError('--tezi must point to the TEZI runtime, not the OS image')
    if args.output.exists():
        raise ValueError('Output directory already exists')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ci4rail-migration-') as temp:
        work = Path(temp).resolve()
        extracted = work / 'payload'
        extracted.mkdir()
        payload = extract_payload(args.payload, extracted)
        manifest, refs, minimum_mib = validate_payload(payload, metadata['supported_product_ids'])
        datafile = payload / manifest['blockdevs'][0]['partitions'][3]['content']['rawfiles'][0]['filename']
        fsinfo = output('dumpe2fs', '-h', datafile)
        free_blocks = int(re.search(r'^Free blocks:\s+(\d+)', fsinfo, re.M)[1])
        block_size = int(re.search(r'^Block size:\s+(\d+)', fsinfo, re.M)[1])
        # Reserve enough room even before enlarging the initial data filesystem.
        data_free_kib = free_blocks * block_size // 1024
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
        if not (rootfs / 'usr/bin/tezictl').is_file() or not (rootfs / 'usr/lib/plugins/platforms/libqoffscreen.so').is_file():
            raise ValueError('TEZI runtime needs tezictl and the offscreen Qt platform')
        if not (rootfs / 'bin/tar.tar').is_file():
            raise ValueError('TEZI runtime needs GNU tar for metadata-preserving backup')
        embedded = rootfs / 'migration'
        embedded.mkdir()
        image = embedded / 'image'
        image.mkdir()
        for filename in refs:
            shutil.copyfile(payload / filename, image / filename)
        for name in ('common.sh', 'prepare.sh', 'wrapup.sh', 'error.sh'):
            target = (embedded if name == 'common.sh' else image) / name
            shutil.copyfile(ROOT / 'runtime' / name, target)
            target.chmod(0o755)
        (image / 'image.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (image / 'image_list.json').write_text(json.dumps({
            'config_format': 1, 'images': ['image.json'],
        }, indent=2) + '\n')
        (image / 'SHA256SUMS').write_text(''.join(
            f'{digest(p)}  {p.name}\n' for p in sorted(image.iterdir())))
        (embedded / 'profile.conf').write_text(
            f"SOC='{profile['soc']}'\nMAX_BACKUP_MIB={profile['maximum_backup_mib']}\n"
            f'MINIMUM_DISK_MIB={minimum_mib}\nDATA_FREE_KIB={data_free_kib}\n')
        shutil.copyfile(ROOT / 'runtime/rc.local', rootfs / 'etc/rc.local')
        http_server = work / 'migration-http'
        run('aarch64-linux-gnu-gcc', '-static', '-Os', '-Wall', '-Wextra', '-Werror', '-s',
            ROOT / 'runtime/local_http.c', '-o', http_server)
        with http_server.open('rb') as stream:
            header = stream.read(20)
        if header[:6] != b'\x7fELF\x02\x01' or header[18:20] != b'\xb7\x00':
            raise ValueError('Expected a 64-bit little-endian AArch64 HTTP server')
        shutil.copyfile(http_server, rootfs / 'usr/bin/migration-http')
        (rootfs / 'usr/bin/migration-http').chmod(0o755)
        (rootfs / 'etc/fw_env.config').write_text(env_config)
        # Avoid mounting old storage or starting network image discovery.
        for base in ('etc/udev/rules.d', 'lib/udev/rules.d', 'usr/lib/udev/rules.d'):
            for pattern in ('*automount*', '*ifplugd*'):
                for rule in (rootfs / base).glob(pattern):
                    rule.unlink()
        (rootfs / 'etc/tezi_config.json').write_text(json.dumps({
            'config_format': 1, 'image_lists': [],
            'show_default_feed': False, 'show_3rdparty_feed': False}))
        squashfs = work / 'migration.squashfs'
        run('mksquashfs', rootfs, squashfs, '-noappend', '-all-root', '-comp', 'gzip',
            '-no-progress', '-processors', '2', stdout=subprocess.DEVNULL)
        dest = work / 'result'
        dest.mkdir()
        # Copy to stable simple names so ITS never embeds unescaped user paths.
        shutil.copyfile(dtb, work / 'board.dtb')
        make_fit(kernel, squashfs, work / 'board.dtb', load, compression, dest / 'migration.itb')
        size = (dest / 'migration.itb').stat().st_size
        if size > profile['maximum_fit_mib'] * MIB:
            raise ValueError(f"Embedded FIT is {size / MIB:.1f} MiB; {args.platform} limit is {profile['maximum_fit_mib']} MiB. Use a smaller payload; this build does not silently switch to external storage.")
        boot_scripts(dest, profile['maximum_fit_mib'], profile['console'])
        (dest / 'build.json').write_text(json.dumps({
            'platform': args.platform, 'profile': profile,
            'payload_sha256': digest(args.payload), 'tezi_fit_sha256': digest(tezi / 'tezi.itb'),
            'dtb_sha256': digest(dtb), 'fit_size_bytes': size,
            'local_http_source_sha256': digest(ROOT / 'runtime/local_http.c'),
            'local_http_sha256': digest(http_server),
            'fw_env_config_sha256': digest(args.fw_env_config),
            'fuse_programming': False, 'preserve': ['/data/mender'],
            'hardware_validated': False,
        }, indent=2) + '\n')
        (dest / 'SHA256SUMS').write_text(''.join(
            f'{digest(p)}  {p.name}\n' for p in sorted(dest.iterdir())))
        shutil.move(dest, args.output)
    print(f'Created {args.output} ({size / MIB:.1f} MiB FIT). Hardware validation still required.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=('cpu01', 'cpu01plus'), required=True)
    parser.add_argument('--tezi', type=Path, required=True, help='Extracted platform TEZI runtime directory')
    parser.add_argument('--dtb', type=Path, required=True, help='ModuCop DTB compatible with the TEZI kernel')
    parser.add_argument('--fw-env-config', type=Path, required=True,
                        help='Exported /etc/fw_env.config from the target OS build')
    parser.add_argument('--payload', type=Path, required=True, help='Trusted current four-partition .mender_tezi.tar')
    parser.add_argument('--output', type=Path, required=True, help='New output directory')
    args = parser.parse_args()
    try:
        build(args)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Migration build failed: {error}\n')


if __name__ == '__main__':
    main()
