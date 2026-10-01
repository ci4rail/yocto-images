"""Execute the real hook bodies with simulated mounts and eMMC sysfs.

No block devices, privileges, or actual mounts are used. tar, its metadata
preservation, checksums, and the phase markers are real.
"""
import hashlib
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
        for name in ('state/old/mender', 'state/new/mender', 'image',
                     'sys/emmc/device', 'proc', 'bin'):
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
        (self.root / 'sys/emmc/size').write_text('10000000\n')
        (self.root / 'sys/emmc/device/cid').write_text('fixture-cid\n')
        (self.root / 'proc/meminfo').write_text('MemAvailable: 1048576 kB\n')
        payload = self.root / 'image/payload'
        payload.write_bytes(b'payload fixture')
        (self.root / 'image/SHA256SUMS').write_text(
            hashlib.sha256(payload.read_bytes()).hexdigest() + '  payload\n')
        (self.root / 'profile.conf').write_text(
            "SOC='fixture'\nMAX_BACKUP_MIB=128\nMINIMUM_DISK_MIB=128\nDATA_FREE_KIB=131072\n")
        common = (RUNTIME / 'common.sh').read_text()
        common = common.replace('/migration/profile.conf', str(self.root / 'profile.conf'))
        common = common.replace('/run/migration', str(self.root / 'state'))
        common = common.replace('/dev/console', str(self.root / 'console'))
        common += f'''
identify_emmc() {{ DEVICE={shlex.quote(str(self.root / 'emmc'))}; DISK=emmc; }}
unmounted_emmc() {{ :; }}
check_platform() {{ :; }}
cleanup_mounts() {{ :; }}
'''
        (self.root / 'common.sh').write_text(common)
        for tool, body in {'mount': 'exit 0', 'umount': 'exit 0',
                           'blkid': 'echo ext4', 'e2fsck': 'exit 0', 'sync': 'exit 0'}.items():
            path = self.root / 'bin' / tool
            path.write_text('#!/bin/sh\n' + body + '\n')
            path.chmod(0o755)

    def hook(self, name):
        text = (RUNTIME / name).read_text()
        for original, replacement in {
            '/migration/common.sh': str(self.root / 'common.sh'),
            '/migration/image': str(self.root / 'image'),
            '/sys/class/block': str(self.root / 'sys'),
            '/proc/meminfo': str(self.root / 'proc/meminfo'),
            '/dev/console': str(self.root / 'console'),
            '[ -b ': '[ -f ',
        }.items():
            text = text.replace(original, replacement)
        script = self.root / name
        script.write_text(text)
        return subprocess.run(['sh', str(script)], capture_output=True, text=True,
                              env={**os.environ, 'PATH': str(self.root / 'bin') + ':' + os.environ['PATH']})

    def test_preserve_and_verify_full_directory_metadata(self):
        result = self.hook('prepare.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'state/prepared').exists())
        result = self.hook('wrapup.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'state/restored').exists())
        self.assertFalse((self.new / 'factory-identity').exists())
        self.assertEqual((self.new / 'mender-agent.pem').read_bytes(), (self.old / 'mender-agent.pem').read_bytes())
        self.assertEqual((self.new / 'mender-agent.pem').stat().st_mode & 0o777, 0o600)
        self.assertEqual(os.getxattr(self.new / 'mender-agent.pem', 'user.migration-test'), b'preserved')
        self.assertEqual(os.readlink(self.new / 'key-link'), 'mender-agent.pem')
        self.assertEqual((self.new / 'database').stat().st_ino, (self.new / 'database-link').stat().st_ino)

    def test_missing_mender_aborts_before_prepared(self):
        (self.old).rename(self.old.with_name('not-mender'))
        result = self.hook('prepare.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No separate legacy data partition', result.stderr)
        self.assertFalse((self.root / 'state/prepared').exists())

    def test_ambiguous_partitions_aborted(self):
        (self.root / 'emmcp2').touch()
        result = self.hook('prepare.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Multiple legacy', result.stderr)
        self.assertFalse((self.root / 'state/prepared').exists())

    def test_corrupt_payload_aborts_before_backup(self):
        (self.root / 'image/payload').write_bytes(b'corrupt')
        result = self.hook('prepare.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'state/backup/mender.tar').exists())

    def test_legacy_data_is_repaired_and_verified_before_backup(self):
        fsck = self.root / 'bin/e2fsck'
        fsck.write_text('''#!/bin/sh
case "$1" in
    -fy) printf 'repaired\\n' >"$FSCK_STATE"; exit 1 ;;
    -fn) [ -e "$FSCK_STATE" ] && exit 0 || exit 4 ;;
esac
exit 8
'''.replace('$FSCK_STATE', shlex.quote(str(self.root / 'fsck-repaired'))))
        result = self.hook('prepare.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'state/legacy-fsck-repair.log').exists())
        self.assertTrue((self.root / 'state/legacy-fsck-verify.log').exists())
        self.assertTrue((self.root / 'state/backup/mender.tar').exists())

    def test_unrepairable_legacy_data_aborts_before_backup(self):
        fsck = self.root / 'bin/e2fsck'
        fsck.write_text('#!/bin/sh\n[ "$1" = -fy ] && exit 4\nexit 4\n')
        result = self.hook('prepare.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Legacy data repair failed', result.stderr)
        self.assertFalse((self.root / 'state/backup/mender.tar').exists())

    def test_repair_requiring_reboot_aborts_before_backup(self):
        fsck = self.root / 'bin/e2fsck'
        fsck.write_text('#!/bin/sh\n[ "$1" = -fy ] && exit 2\nexit 4\n')
        result = self.hook('prepare.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('e2fsck exit 2', result.stderr)
        self.assertFalse((self.root / 'state/backup/mender.tar').exists())

    def test_fsck_operational_failure_does_not_attempt_repair(self):
        fsck = self.root / 'bin/e2fsck'
        marker = self.root / 'repair-attempted'
        fsck.write_text('#!/bin/sh\n[ "$1" = -fy ] && touch ' +
                        shlex.quote(str(marker)) + '\nexit 8\n')
        result = self.hook('prepare.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('e2fsck exit 8', result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / 'state/backup/mender.tar').exists())

    def test_repair_must_pass_followup_check(self):
        fsck = self.root / 'bin/e2fsck'
        fsck.write_text('#!/bin/sh\n[ "$1" = -fy ] && exit 1\nexit 4\n')
        result = self.hook('prepare.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('still has errors', result.stderr)
        self.assertFalse((self.root / 'state/backup/mender.tar').exists())

    def test_insufficient_ram_aborted(self):
        (self.root / 'proc/meminfo').write_text('MemAvailable: 100 kB\n')
        result = self.hook('prepare.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Insufficient free RAM', result.stderr)

    def test_corrupt_backup_does_not_replace_new_data(self):
        self.assertEqual(self.hook('prepare.sh').returncode, 0)
        (self.root / 'state/backup/mender.tar').write_bytes(b'corrupt')
        result = self.hook('wrapup.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.new / 'factory-identity').exists())
        self.assertFalse((self.root / 'state/restored').exists())

    def test_rerun_cannot_overwrite_backup(self):
        self.assertEqual(self.hook('prepare.sh').returncode, 0)
        backup = self.root / 'state/backup/mender.tar'
        previous = backup.read_bytes()
        self.assertNotEqual(self.hook('prepare.sh').returncode, 0)
        self.assertEqual(backup.read_bytes(), previous)


if __name__ == '__main__':
    unittest.main()
