import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('bundle', Path(__file__).resolve().parents[1] / 'bundle.py')
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.boot = self.root / 'boot'
        self.runtime = self.root / 'runtime'
        self.boot.mkdir()
        self.runtime.mkdir()
        self.config = '\n'.join((
            'CONFIG_IMX_HAB=y', 'CONFIG_FIT_SIGNATURE=y',
            'CONFIG_TDX_SECBOOT_HARDENING=y', 'CONFIG_TDX_FB_PROTECTION=y',
            'CONFIG_TDX_FB_PROTECTION_ALLOW_CMD=y', 'CONFIG_TARGET_VERDIN_IMX8MM=y',
            'CONFIG_FASTBOOT_BUF_ADDR=0x60000000', 'CONFIG_FASTBOOT_BUF_SIZE=0x10000000'))
        (self.boot / 'u-boot.config').write_text(self.config)
        (self.boot / 'u-boot.dtb').write_bytes(b'test public DTB')
        (self.boot / 'srk-fuse.bin').write_bytes(bytes(32))
        (self.boot / 'csf-spl.bin').write_bytes(b'\xd4\x00\x04\x40')
        (self.boot / 'csf-fit.bin').write_bytes(b'\xd4\x00\x04\x41')
        (self.boot / 'imx-boot-recovery').write_bytes(b'header\xd4\x00\x04\x40\xd4\x00\x04\x41')
        (self.runtime / 'recovery.itb').write_bytes(b'test FIT')
        self.metadata = dict(platform='cpu01', signing_mode='staging', signature_verified=True,
                             version='test', fit_spki_sha256='test')
        self.write_metadata()
        self.key_check = patch.object(bundle.subprocess, 'check_output', return_value='conf\n')
        self.key_check.start()
        self.addCleanup(self.key_check.stop)

    def write_metadata(self):
        (self.runtime / 'build.json').write_text(json.dumps(self.metadata))

    def validate(self):
        return bundle.validate_inputs(self.boot, self.runtime, 'cpu01', 'staging')

    def test_verifies_fit_against_built_bootloader(self):
        with patch.object(bundle.subprocess, 'run') as run:
            self.validate()
        self.assertEqual(run.call_args.args[0], [
            'fit_check_sign', '-f', str(self.runtime / 'recovery.itb'),
            '-k', str(self.boot / 'u-boot.dtb'), '-c', 'recovery'])
        self.assertTrue(run.call_args.kwargs['check'])

    def test_optional_fit_key_rejected(self):
        with patch.object(bundle.subprocess, 'check_output', return_value='image\n'):
            with self.assertRaisesRegex(ValueError, 'not mandatory'):
                self.validate()

    def test_bad_fit_signature_rejected(self):
        with patch.object(bundle.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'fit_check_sign')):
            with self.assertRaises(subprocess.CalledProcessError):
                self.validate()

    def test_wrong_platform_or_mode_rejected(self):
        for field, value in [('platform', 'cpu01plus'), ('signing_mode', 'production')]:
            with self.subTest(field=field):
                previous = self.metadata[field]
                self.metadata[field] = value
                self.write_metadata()
                with self.assertRaises(ValueError):
                    self.validate()
                self.metadata[field] = previous

    def test_missing_hab_signature_rejected(self):
        (self.boot / 'imx-boot-recovery').write_bytes(b'unsigned')
        with self.assertRaisesRegex(ValueError, 'Missing HAB CSF'):
            self.validate()

    def test_unrestricted_fastboot_rejected(self):
        (self.boot / 'u-boot.config').write_text(self.config + '\nCONFIG_FASTBOOT_UUU_SUPPORT=y')
        with self.assertRaisesRegex(ValueError, 'Unrestricted'):
            self.validate()

    def test_undefined_production_platform_rejected(self):
        config = self.root / 'platforms.json'
        config.write_text(json.dumps({'platforms': {'cpu01plus': {'signing': {}}}}))
        with patch.object(bundle.recovery, 'CONFIG', config), \
                self.assertRaisesRegex(ValueError, 'no reviewed signing'):
            bundle.validate_inputs(self.boot, self.runtime, 'cpu01plus', 'production')

    def test_platform_transport_and_no_script_execution(self):
        self.assertIn('SDP: boot', bundle.uuu_script('cpu01'))
        self.assertIn('SDPV: write', bundle.uuu_script('cpu01'))
        self.assertIn('SDPS: boot', bundle.uuu_script('cpu01plus'))
        self.assertIn('CFG: FB: -vid 0x1b67 -pid 0x4037\n', bundle.uuu_script('cpu01'))
        self.assertIn('CFG: FB: -vid 0x1b67 -pid 0x403a\n', bundle.uuu_script('cpu01plus'))
        for platform in ('cpu01', 'cpu01plus'):
            script = bundle.uuu_script(platform)
            self.assertEqual(script.splitlines()[0], 'uuu_version 1.4.127')
            self.assertIn('CFG: FB: -vid 0x1b67 -pid 0x4000\n', script)
            self.assertIn('FB: continue', script)
            for forbidden in ('ucmd', 'acmd', 'flash ', 'erase ', 'boot.scr', 'fuse '):
                self.assertNotIn(forbidden, script.lower())

    def test_package_checksums_and_no_private_material(self):
        from argparse import Namespace
        revisions = self.root / 'revisions.json'
        revisions.write_text('{"meta-example": "revision"}')
        output = self.root / 'recovery-bundle.tar.gz'
        args = Namespace(bootloader=self.boot, runtime=self.runtime, platform='cpu01',
                         mode='staging', output=output, revisions=revisions)
        with patch.object(bundle.subprocess, 'run'), patch.dict(os.environ, {
                'GITHUB_SHA': 'commit', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}):
            bundle.package(args)
        with tarfile.open(output) as tar:
            names = tar.getnames()
            self.assertIn(b'CFG: FB: -vid 0x1b67 -pid 0x4037\n',
                          tar.extractfile('recovery/uuu.auto').read())
            self.assertEqual(json.load(tar.extractfile('build.json'))['uuu_min_version'], '1.4.127')
            self.assertEqual(set(names), {'imx-boot-recovery', 'recovery.itb', 'recovery/uuu.auto',
                'u-boot.config', 'u-boot.dtb', 'srk-fuse.bin', 'runtime-build.json', 'build.json', 'SHA256SUMS'})
            for line in tar.extractfile('SHA256SUMS').read().decode().splitlines():
                checksum, name = line.split('  ')
                self.assertEqual(checksum, bundle.hashlib.sha256(tar.extractfile(name).read()).hexdigest())
            manifest = json.load(tar.extractfile('build.json'))
            for name, checksum in manifest['files'].items():
                self.assertEqual(checksum, bundle.hashlib.sha256(tar.extractfile(name).read()).hexdigest())
        self.assertTrue(output.with_suffix('.gz.sha256').exists())
        self.assertEqual(output.with_suffix('.gz.sha256').read_text(),
                         f'{bundle.digest(output)}  {output.name}\n')


if __name__ == '__main__':
    unittest.main()
