"""Execute manual backup/restore with simulated mounts and eMMC sysfs.

No block devices, privileges, or actual mounts are used. tar, its metadata
preservation, checksums, and the completion markers are real.
"""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

RUNTIME = Path(__file__).parents[1] / 'runtime'


class PreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('state/old/mender', 'state/new/mender', 'media/backups',
                     'sys/emmc/device', 'bin'):
            (self.root / name).mkdir(parents=True)
        self.old = self.root / 'state/old/mender'
        self.new = self.root / 'state/new/mender'
        key = self.old / 'mender-agent.pem'
        key.write_bytes(b'private key fixture\n')
        key.chmod(0o600)
        os.setxattr(key, 'user.migration-test', b'preserved')
        (self.old / 'database').write_bytes(b'identity database fixture\n')
        os.link(self.old / 'database', self.old / 'database-link')
        (self.old / 'key-link').symlink_to('mender-agent.pem')
        (self.new / 'factory-identity').write_bytes(b'must be removed on restoration')
        (self.root / 'emmc').touch()
        (self.root / 'emmcp1').touch()
        (self.root / 'sys/emmc/device/cid').write_text('fixture-cid\n')
        (self.root / 'profile.conf').write_text("SOC='fixture'\n")
        common = (RUNTIME / 'common.sh').read_text()
        common = common.replace('/migration/profile.conf', str(self.root / 'profile.conf'))
        common = common.replace('/run/migration', str(self.root / 'state'))
        common = common.replace('/dev/console', str(self.root / 'console'))
        common = common.replace('TAR=/bin/tar.tar', 'TAR=tar')
        common += f'''
identify_emmc() {{ DEVICE={shlex.quote(str(self.root / 'emmc'))}; DISK=emmc; }}
unmounted_emmc() {{ :; }}
check_platform() {{ :; }}
cleanup_mounts() {{ :; }}
backup_folder() {{ FOLDER=$1; }}
select_data_partition() {{ identify_emmc; PARTITION=$1; TYPE=ext4; }}
check_data_filesystem() {{ :; }}
'''
        (self.root / 'common.sh').write_text(common)
        subprocess.run(['cc', '-DBACKUP_CHUNK_BYTES=131072', '-Wall', '-Wextra', '-Werror', str(RUNTIME / 'split-backup.c'), '-o', str(self.root / 'bin/split-backup')], check=True)
        (self.old.parent / '.hidden').write_text('outside mender')
        for tool, body in {'mount': 'exit 0', 'umount': 'exit 0',
                           'blkid': 'echo ext4', 'e2fsck': 'exit 0', 'sync': 'exit 0'}.items():
            path = self.root / 'bin' / tool
            path.write_text('#!/bin/sh\n' + body + '\n')
            path.chmod(0o755)

    def run_script(self, name):
        text = (RUNTIME / name).read_text()
        for original, replacement in {
            '/migration/split-backup': str(self.root / 'bin/split-backup'),
            '/migration/common.sh': str(self.root / 'common.sh'),
            '/sys/class/block': str(self.root / 'sys'),
            '/dev/console': str(self.root / 'console'),
            '[ -b ': '[ -f ',
        }.items():
            text = text.replace(original, replacement)
        script = self.root / name
        script.write_text(text)
        return subprocess.run(['sh', str(script), str(self.root / 'media/backups/test'), str(self.root / 'emmcp1')], capture_output=True, text=True,
                              env={**os.environ, 'PATH': str(self.root / 'bin') + ':' + os.environ['PATH']})

    def test_preserve_entire_data_and_metadata(self):
        result = self.run_script('backup-data.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_script('restore-data.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.new / 'factory-identity').exists())
        self.assertEqual((self.new.parent / '.hidden').read_text(), 'outside mender')
        self.assertEqual((self.new / 'mender-agent.pem').stat().st_mode & 0o777, 0o600)
        self.assertEqual(os.getxattr(self.new / 'mender-agent.pem', 'user.migration-test'), b'preserved')
        self.assertEqual(os.readlink(self.new / 'key-link'), 'mender-agent.pem')
        self.assertEqual((self.new / 'database').stat().st_ino, (self.new / 'database-link').stat().st_ino)

    def test_corrupt_backup_does_not_replace_data(self):
        self.assertEqual(self.run_script('backup-data.sh').returncode, 0)
        (self.root / 'media/backups/test/data.tar.part-0000').write_bytes(b'corrupt')
        self.assertNotEqual(self.run_script('restore-data.sh').returncode, 0)
        self.assertTrue((self.new / 'factory-identity').exists())

    def test_rerun_preserves_backup(self):
        self.assertEqual(self.run_script('backup-data.sh').returncode, 0)
        backup = self.root / 'media/backups/test/data.tar.part-0000'
        original = backup.read_bytes()
        self.assertNotEqual(self.run_script('backup-data.sh').returncode, 0)
        self.assertEqual(backup.read_bytes(), original)

    def test_incomplete_backup_rejected(self):
        self.assertEqual(self.run_script('backup-data.sh').returncode, 0)
        (self.root / 'media/backups/test/complete').unlink()
        self.assertNotEqual(self.run_script('restore-data.sh').returncode, 0)
        self.assertTrue((self.new / 'factory-identity').exists())

    def test_wrong_device_backup_rejected(self):
        self.assertEqual(self.run_script('backup-data.sh').returncode, 0)
        (self.root / 'media/backups/test/emmc-cid').write_text('other')
        self.assertNotEqual(self.run_script('restore-data.sh').returncode, 0)
        self.assertTrue((self.new / 'factory-identity').exists())

    def test_chunk_boundaries_and_existing_file_protection(self):
        prefix = self.root / 'chunks-'
        content = bytes(range(256)) * 1100
        result = subprocess.run([str(self.root / 'bin/split-backup'), str(prefix)],
                                input=content, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        chunks = sorted(self.root.glob('chunks-*'))
        self.assertEqual([p.stat().st_size for p in chunks], [131072, 131072, 19456])
        self.assertEqual(b''.join(p.read_bytes() for p in chunks), content)
        result = subprocess.run([str(self.root / 'bin/split-backup'), str(prefix)],
                                input=b'replacement', capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(b''.join(p.read_bytes() for p in chunks), content)

    def test_insufficient_space_does_not_replace_data(self):
        self.assertEqual(self.run_script('backup-data.sh').returncode, 0)
        df = self.root / 'bin/df'
        df.write_text('#!/bin/sh\necho filesystem 100 100 0 100% /\n')
        df.chmod(0o755)
        result = self.run_script('restore-data.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('too small', result.stderr)
        self.assertTrue((self.new / 'factory-identity').exists())
