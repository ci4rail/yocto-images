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
        return build.validate_payload(self.root, ['0055'])

    def test_current_layout_normalized_without_changing_contents(self):
        original = copy.deepcopy(self.data)
        result, refs, size = self.validate()
        self.assertEqual(result['blockdevs'][0]['name'], 'emmc')
        self.assertEqual(result['blockdevs'][1]['name'], 'emmc-boot0')
        self.assertEqual(result['blockdevs'][0]['partitions'], original['blockdevs'][0]['partitions'])
        self.assertFalse(result['autoinstall'])
        self.assertEqual(result['prepare_script'], 'prepare.sh')
        self.assertNotIn('old-prepare.sh', refs)
        self.assertGreater(size, 256)

    def test_wrong_platform(self):
        self.data['supported_product_ids'] = ['0063']
        with self.assertRaisesRegex(ValueError, 'product IDs'):
            self.validate()

    def test_unknown_hooks_not_executed(self):
        self.data['error_script'] = 'fuse.sh'
        result, refs, _ = self.validate()
        self.assertEqual(result['error_script'], 'error.sh')
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

    def test_target_environment_layout(self):
        path = self.root / 'fw_env.config'
        path.write_text('/dev/mmcblk2boot0 -0x2200 0x2000\n/dev/mmcblk2boot0 -0x4200 0x2000\n')
        self.assertIn('/dev/emmc-boot0', build.validate_env_config(path))
        path.write_text('/dev/mmcblk2 0x400000 0x20000\n')
        with self.assertRaises(ValueError):
            build.validate_env_config(path)


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
