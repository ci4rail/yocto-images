import copy
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('migration_build', Path(__file__).parents[1] / 'build.py')
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def manifest():
    raw = lambda f: {'filesystem_type': 'raw', 'rawfiles': [{'filename': f}], 'uncompressed_size': 1}
    return {
        'config_format': '4', 'name': 'test', 'supported_product_ids': ['0055'],
        'autoinstall': True, 'u_boot_env': 'u-boot-env', 'prepare_script': 'old-prepare.sh',
        'wrapup_script': 'old-wrapup.sh',
        'blockdevs': [
            {'name': 'mmcblk0', 'partitions': [
                {'partition_size_nominal': 64, 'partition_type': '83', 'want_maximised': False,
                 'offset_in_sectors': 49152,
                 'content': {'label': 'BOOTFIT', 'filesystem_type': 'ext4', 'mkfs_options': '',
                             'filename': 'boot.tar.xz', 'uncompressed_size': 1}},
                {'partition_size_nominal': 64, 'partition_type': '83', 'want_maximised': False,
                 'content': raw('rootfs.ext4')},
                {'partition_size_nominal': 64, 'partition_type': '83', 'want_maximised': False,
                 'content': raw('rootfs.ext4')},
                {'partition_size_nominal': 64, 'partition_type': '83', 'want_maximised': True,
                 'content': raw('data.img')},
            ]},
            {'name': 'mmcblk0boot0', 'erase': True,
             'content': {'filesystem_type': 'raw', 'rawfiles': [
                 {'filename': 'imx-boot', 'dd_options': 'seek=0'}]}},
        ],
    }


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = manifest()
        for name in ('boot.tar.xz', 'rootfs.ext4', 'data.img', 'imx-boot'):
            (self.root / name).write_bytes(b'fixture')
        (self.root / 'u-boot-env').write_text('bootcmd=run mender_setup\n')

    def validate(self):
        (self.root / 'image.json').write_text(json.dumps(self.data))
        return build.validate_payload(self.root)

    def test_current_layout_normalized_without_changing_contents(self):
        original = copy.deepcopy(self.data)
        result, refs = self.validate()
        self.assertEqual(result['blockdevs'][0]['name'], 'emmc')
        self.assertEqual(result['blockdevs'][1]['name'], 'emmc-boot0')
        self.assertEqual(result['blockdevs'][0]['partitions'], original['blockdevs'][0]['partitions'])
        self.assertFalse(result['autoinstall'])
        self.assertNotIn('prepare_script', result)
        self.assertNotIn('wrapup_script', result)
        self.assertNotIn('old-prepare.sh', refs)

    def test_product_ids_preserved(self):
        self.data['supported_product_ids'] = ['0063', '0055']
        result, _ = self.validate()
        self.assertEqual(result['supported_product_ids'], ['0063', '0055'])

    def test_invalid_product_ids_rejected(self):
        for ids in (None, [], '0055', [55], [''], ['   ']):
            with self.subTest(ids=ids):
                self.data['supported_product_ids'] = ids
                with self.assertRaisesRegex(ValueError, 'product IDs'):
                    self.validate()

    def test_payload_build_without_tezi(self):
        self.validate()
        archive = self.root / 'release.tar'
        with tarfile.open(archive, 'w') as tf:
            for path in sorted(self.root.iterdir()):
                if path != archive:
                    tf.add(path, arcname='release/' + path.name)
        destination = self.root / 'output'
        args = build.argument_parser('payload').parse_args([
            '--payload', str(archive), '--output', str(destination)])
        self.assertFalse(hasattr(args, 'tezi'))
        build.build(args, 'payload')
        result = json.loads((destination / 'image.json').read_text())
        self.assertEqual(result['supported_product_ids'], self.data['supported_product_ids'])
        self.assertFalse(result['autoinstall'])
        self.assertEqual((destination / 'rootfs.ext4').read_bytes(), b'fixture')
        self.assertTrue((destination / 'SHA256SUMS').is_file())

    def test_unknown_hooks_not_executed(self):
        self.data['error_script'] = 'fuse.sh'
        result, refs = self.validate()
        self.assertNotIn('error_script', result)
        self.assertNotIn('fuse.sh', refs)

    def test_fusing_environment_rejected(self):
        (self.root / 'u-boot-env').write_text('bootcmd=run fuse_provision\n')
        with self.assertRaisesRegex(ValueError, 'Fuse-related'):
            self.validate()

    def test_bootpart_environment_allowed(self):
        (self.root / 'u-boot-env').write_text('distro_bootpart=1\n')
        self.validate()

    def test_otp_environment_rejected(self):
        (self.root / 'u-boot-env').write_text('bootcmd=run program_otp\n')
        with self.assertRaisesRegex(ValueError, 'Fuse-related'):
            self.validate()

    def test_unsupported_formats_and_writes_rejected(self):
        mutations = [
            lambda m: m.update(mtddevs=[]),
            lambda m: m.update(config_format=5),
            lambda m: m['blockdevs'][0].update(name='sda'),
            lambda m: m['blockdevs'][0]['partitions'].pop(),
            lambda m: m['blockdevs'][0]['partitions'][3].update(want_maximised=False),
            lambda m: m['blockdevs'][0]['partitions'][2]['content']['rawfiles'][0].update(filename='other'),
            lambda m: m['blockdevs'][1]['content']['rawfiles'][0].update(dd_options='seek=0; reboot'),
            lambda m: m['blockdevs'][0]['partitions'][0]['content'].update(filename='../outside'),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.data = manifest()
                mutate(self.data)
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.validate()

    def test_oversized_raw_image_rejected(self):
        with (self.root / 'rootfs.ext4').open('wb') as stream:
            stream.truncate(65 * build.MIB)
        with self.assertRaisesRegex(ValueError, 'larger'):
            self.validate()


class ConsoleAuthTests(unittest.TestCase):
    password_hash = '$6$salt$' + 'a' * 86

    def test_hash_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'hash'
            path.write_text(self.password_hash + '\n')
            self.assertEqual(build.read_root_password_hash(path), self.password_hash)
            for invalid in ('', 'password', '!', self.password_hash + '\nroot::',
                            self.password_hash + ':', '$6$salt$short'):
                path.write_text(invalid)
                with self.assertRaises(ValueError):
                    build.read_root_password_hash(path)

    def test_console_authentication_preserves_respawn(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'sbin').mkdir()
            (root / 'etc').mkdir()
            init = root / 'sbin/init'
            init.write_text('while true; do\n\tsetsid cttyhack sh\n\tsleep 1\ndone\n')
            init.chmod(0o755)
            (root / 'sbin/sulogin.util-linux').touch()
            shadow = root / 'etc/shadow'
            shadow.write_text('root::0:0:99999:7:::\nother:!:0:0:99999:7:::\n')
            shadow.chmod(0o400)  # Vendor TEZI shadow is read-only after extraction.
            build.configure_console_auth(root, self.password_hash)
            self.assertEqual(init.read_text(),
                             'while true; do\n\tsetsid cttyhack /sbin/sulogin.util-linux\n\tsleep 1\ndone\n')
            self.assertEqual(init.stat().st_mode & 0o777, 0o755)
            self.assertEqual(shadow.stat().st_mode & 0o777, 0o600)
            self.assertEqual(shadow.read_text(),
                             f'root:{self.password_hash}:0:0:99999:7:::\nother:!:0:0:99999:7:::\n')
            with self.assertRaisesRegex(ValueError, 'Unsupported TEZI init'):
                build.configure_console_auth(root, self.password_hash)


class RecoveryInputTests(unittest.TestCase):
    def test_signed_recovery_needs_no_bootloader_or_exported_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'image.json').write_text(json.dumps({'isinstaller': True}))
            args = build.argument_parser('recovery').parse_args([
                '--platform', 'cpu01', '--tezi', str(root), '--dtb', str(root / 'board.dtb'),
                '--root-password-hash-file', str(root / 'root.hash'),
                '--output', str(root / 'output'), '--fit-keydir', str(root / 'keys')])
            with patch.object(build, 'require_tools'), patch.object(build, 'output', side_effect=[
                    'fsl,imx8mm', 'ModuCop CPU01', 'verdin-imx8mm']):
                self.assertEqual(build.validate_recovery_inputs(args, {'soc': 'fsl,imx8mm'}),
                                 (root, root / 'board.dtb'))
            args.fit_keydir = None
            with self.assertRaisesRegex(ValueError, 'requires --fit-keydir'):
                build.validate_recovery_inputs(args, {'soc': 'fsl,imx8mm'})

    def test_runtime_installs_redundant_environment_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'etc/network').mkdir(parents=True)
            (root / 'etc/fw_env.config').write_text('/dev/emmc-boot0 -0x2200 0x2000\n')
            with patch.object(build, 'run'):
                build.configure_runtime(root, {'soc': 'fsl,imx8mm'})
            rows = [line.split() for line in (root / 'etc/fw_env.config').read_text().splitlines()
                    if line and not line.startswith('#')]
            self.assertEqual(rows, [['/dev/emmc-boot0', '-0x2200', '0x2000'],
                                    ['/dev/emmc-boot0', '-0x4200', '0x2000']])


@unittest.skipUnless(all(shutil.which(t) for t in ('mkimage', 'fdtget', 'fdtput', 'dtc', 'openssl')),
                     'FIT host tools required')
class RecoveryFitTests(unittest.TestCase):
    def test_signed_recovery_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kernel, ramdisk, dtb = [root / n for n in ('kernel', 'ramdisk', 'board.dtb')]
            kernel.write_bytes(b'kernel fixture')
            ramdisk.write_bytes(b'rootfs fixture')
            subprocess.run(['dtc', '-O', 'dtb', '-o', str(dtb)],
                           input='/dts-v1/; / {};', text=True, check=True)
            build.prepare_recovery_dtb(dtb, 'ttymxc0')
            self.assertEqual(build.output('fdtget', dtb, '/chosen/toradex,secure-boot',
                                          'required-bootargs'), build.recovery_bootargs('ttymxc0'))
            subprocess.run(['openssl', 'req', '-new', '-x509', '-newkey', 'rsa:2048',
                            '-nodes', '-subj', '/CN=test/', '-keyout', str(root / 'dev.key'),
                            '-out', str(root / 'dev.crt'), '-days', '1'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            fit = root / 'recovery.itb'
            build.make_fit(kernel, ramdisk, dtb, 0x48200000, 'none', fit, str(root))
            self.assertEqual(build.output('fdtget', fit, '/configurations', 'default'), 'recovery')
            node = '/configurations/recovery/signature'
            self.assertEqual(build.output('fdtget', fit, node, 'sign-images'), 'kernel fdt ramdisk')
            self.assertEqual(len(build.output('fdtget', '-t', 'bx', fit, node, 'value').split()), 256)
            signed_nodes = build.output('fdtget', fit, node, 'hashed-nodes')
            for image in ('kernel', 'fdt', 'ramdisk'):
                self.assertIn('/images/' + image + '/hash', signed_nodes)
            build.boot_scripts(root, 'ttymxc0')
            self.assertIn('load mmc 1:1 0x60000000 /recovery.itb', (root / 'sd.cmd').read_text())
            self.assertIn('bootm 0x60000000#recovery', (root / 'sd.cmd').read_text())
            # mkimage can leave a hash-only FIT when signing fails; never accept it.
            missing = root / 'missing'
            missing.mkdir()
            with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                build.make_fit(kernel, ramdisk, dtb, 0x48200000, 'none', fit, str(missing))


class ArchiveTests(unittest.TestCase):
    def test_unsafe_members_rejected_before_any_extraction(self):
        for kind in ('traversal', 'absolute', 'symlink', 'hardlink', 'device', 'duplicate'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                archive = root / 'image.tar'
                with tarfile.open(archive, 'w') as tf:
                    regular = tarfile.TarInfo('image/image.json')
                    regular.size = 2
                    tf.addfile(regular, io.BytesIO(b'{}'))
                    bad = tarfile.TarInfo({'traversal': '../outside', 'absolute': '/outside',
                                          'duplicate': 'image/image.json'}.get(kind, 'image/bad'))
                    bad.type = {'symlink': tarfile.SYMTYPE, 'hardlink': tarfile.LNKTYPE,
                                'device': tarfile.CHRTYPE}.get(kind, tarfile.REGTYPE)
                    bad.linkname = '/outside'
                    tf.addfile(bad)
                dest = root / 'unpacked'
                dest.mkdir()
                with self.assertRaises(ValueError):
                    build.extract_payload(archive, dest)
                self.assertEqual(list(dest.iterdir()), [])

    def test_single_nested_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'image.tar'
            with tarfile.open(archive, 'w') as tf:
                info = tarfile.TarInfo('release/image.json')
                info.size = 2
                tf.addfile(info, io.BytesIO(b'{}'))
            dest = root / 'out'
            dest.mkdir()
            self.assertEqual(build.extract_payload(archive, dest), dest / 'release')


if __name__ == '__main__':
    unittest.main()
