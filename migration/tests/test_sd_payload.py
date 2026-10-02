"""Exercise SD discovery using simulated block devices and mounts."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

RUNTIME = Path(__file__).parents[1] / 'runtime'


class SdPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / 'media/image'
        self.image.mkdir(parents=True)
        (self.image / 'payload').write_bytes(b'payload')
        (self.root / 'dev').mkdir()
        for name in ('mmcblk0', 'mmcblk0p1', 'mmcblk1', 'mmcblk1p1'):
            (self.root / 'dev' / name).touch()
        (self.root / 'run').mkdir()
        for name, kind in [('mmcblk0', 'MMC'), ('mmcblk1', 'SD')]:
            device = self.root / 'sys' / name / 'device'
            device.mkdir(parents=True)
            (device / 'type').write_text(kind + '\n')
        binaries = self.root / 'bin'
        binaries.mkdir()
        for name in ('mount', 'umount'):
            path = binaries / name
            path.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$MOUNT_LOG"\n')
            path.chmod(0o755)

        (binaries / 'blkid').write_text('#!/bin/sh\ncat "$TEST_ROOT/filesystem" 2>/dev/null\n')
        (binaries / 'blkid').chmod(0o755)
        (binaries / 'sleep').write_text('#!/bin/sh\n'
            'if [ -f "$TEST_ROOT/delayed" ]; then touch "$TEST_ROOT/dev/mmcblk1p1"; fi\n')
        (binaries / 'sleep').chmod(0o755)

    def run_startup(self):
        source = (RUNTIME / 'mount-sd.sh').read_text()
        for old, new in {
            '/sys/class/block': str(self.root / 'sys'),
            '/run/media/migration-sd': str(self.root / 'media'),
            '/dev/console': str(self.root / 'console'),
            '/dev/': str(self.root / 'dev') + '/',
            '/run/migration/': str(self.root / 'run') + '/',
            '[ -b ': '[ -f ',
            '[ ! -b ': '[ ! -f ',
        }.items():
            source = source.replace(old, new)
        script = self.root / 'startup.sh'
        script.write_text(source)
        return subprocess.run(['sh', str(script)], capture_output=True, text=True,
                              env={**os.environ, 'PATH': str(self.root / 'bin') + ':' + os.environ['PATH'],
                                   'MOUNT_LOG': str(self.root / 'mount.log'), 'TEST_ROOT': str(self.root)})

    def test_only_sd_partition_one_is_mounted_writable(self):
        result = self.run_startup()
        self.assertEqual(result.returncode, 0, result.stderr)
        log = (self.root / 'mount.log').read_text()
        self.assertIn('-o rw,nosuid,nodev ' + str(self.root / 'dev/mmcblk1p1'), log)
        self.assertNotIn('mmcblk0', log)

    def test_mount_does_not_require_a_payload(self):
        (self.image / 'payload').unlink()
        self.assertEqual(self.run_startup().returncode, 0)

    def test_emmc_never_used_when_sd_absent(self):
        (self.root / 'sys/mmcblk1/device/type').write_text('MMC\n')
        self.assertNotEqual(self.run_startup().returncode, 0)
        self.assertFalse((self.root / 'mount.log').exists())

    def test_delayed_partition_is_retried(self):
        (self.root / 'dev/mmcblk1p1').unlink()
        (self.root / 'delayed').touch()
        self.assertEqual(self.run_startup().returncode, 0)
        self.assertIn('mmcblk1p1', (self.root / 'mount.log').read_text())

    def test_whole_card_fat_is_supported(self):
        (self.root / 'dev/mmcblk1p1').unlink()
        (self.root / 'filesystem').write_text('vfat\n')
        self.assertEqual(self.run_startup().returncode, 0)
        log = (self.root / 'mount.log').read_text()
        self.assertIn(str(self.root / 'dev/mmcblk1') + ' ', log)
        self.assertNotIn('mmcblk1p1', log)

    def test_whole_card_nonfat_is_rejected(self):
        (self.root / 'dev/mmcblk1p1').unlink()
        (self.root / 'filesystem').write_text('ext4\n')
        self.assertNotEqual(self.run_startup().returncode, 0)
        self.assertFalse((self.root / 'mount.log').exists())
