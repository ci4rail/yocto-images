"""Run the unattended embedded-payload installer against a simulated TEZI.

tezictl, ip, wget, the local HTTP server and reboot are stubs; the payload
checksums and the installer's control flow are real.
"""
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

RUNTIME = Path(__file__).parents[1] / 'runtime'


class InstallEmbeddedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('state', 'image', 'net/usb0', 'bin'):
            (self.root / name).mkdir(parents=True)
        payload = b'rootfs fixture'
        (self.root / 'image/rootfs.ext4').write_bytes(payload)
        (self.root / 'image/SHA256SUMS').write_text(
            f'{hashlib.sha256(payload).hexdigest()}  rootfs.ext4\n')
        (self.root / 'profile.conf').write_text("SOC='fixture'\n")
        common = (RUNTIME / 'common.sh').read_text()
        common = common.replace('/migration/profile.conf', str(self.root / 'profile.conf'))
        common = common.replace('/run/migration', str(self.root / 'state'))
        common = common.replace('/dev/console', str(self.root / 'console'))
        common += '\nidentify_emmc() { :; }\ncheck_platform() { :; }\n'
        (self.root / 'common.sh').write_text(common)
        # TEZI reports idle until image-install, then the outcome in $TEZI_RESULT.
        self.tool('tezictl', f'''
state={shlex.quote(str(self.root / 'tezi-status'))}
case "$1" in
    status) cat "$state" 2>/dev/null || echo idle ;;
    image-install) echo "$2" >> "$TEST_ROOT/installs"; echo "$TEZI_RESULT" > "$state" ;;
esac''')
        self.tool('reboot', 'echo "$*" >> "$TEST_ROOT/reboots"')
        self.tool('local-http', 'echo "$*" > "$TEST_ROOT/http-args"')
        for name in ('ip', 'wget', 'sleep', 'sync'):
            self.tool(name, 'exit 0')

    def tool(self, name, body):
        path = self.root / 'bin' / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)

    def run_installer(self, result='installed'):
        text = (RUNTIME / 'install-embedded.sh').read_text()
        for original, replacement in {
            '/migration/common.sh': str(self.root / 'common.sh'),
            '/migration/local-http': str(self.root / 'bin/local-http'),
            '/migration/image': str(self.root / 'image'),
            '/sys/class/net': str(self.root / 'net'),
            '/usr/bin/tezictl': 'tezictl',
            '/dev/console': str(self.root / 'console'),
        }.items():
            text = text.replace(original, replacement)
        script = self.root / 'install.sh'
        script.write_text(text)
        return subprocess.run(['sh', str(script)], capture_output=True, text=True, env={
            **os.environ, 'PATH': str(self.root / 'bin') + ':' + os.environ['PATH'],
            'TEST_ROOT': str(self.root), 'TEZI_RESULT': result})

    def read(self, name):
        path = self.root / name
        return path.read_text() if path.exists() else ''

    def test_success_installs_embedded_image_then_reboots(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read('installs'), 'http://192.168.11.1:8765/image.json\n')
        self.assertEqual(self.read('http-args').split()[1:], ['192.168.11.1', '8765'])
        self.assertEqual(self.read('reboots'), '-f\n')
        self.assertIn('Installation complete', self.read('console'))

    def test_failed_installation_never_reboots(self):
        for status in ('failed', 'aborted', 'idle', ''):
            with self.subTest(status=status):
                (self.root / 'tezi-status').unlink(missing_ok=True)
                result = self.run_installer(status)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.read('reboots'), '')
                self.assertIn('Installation failed', self.read('console'))

    def test_corrupt_payload_is_not_installed(self):
        (self.root / 'image/rootfs.ext4').write_bytes(b'modified')
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.read('installs'), '')
        self.assertEqual(self.read('reboots'), '')
        self.assertIn('checksum failed', self.read('console'))

    def test_missing_usb_network_is_not_installed(self):
        (self.root / 'net/usb0').rmdir()
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.read('installs'), '')
        self.assertIn('USB network interface is missing', self.read('console'))


if __name__ == '__main__':
    unittest.main()
