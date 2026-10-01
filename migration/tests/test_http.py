from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@unittest.skipUnless(shutil.which('gcc'), 'host C compiler unavailable')
class LocalHttpTests(unittest.TestCase):
    def test_serves_only_regular_named_files_with_length_and_range(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'image.json').write_bytes(b'{"ok":true}\n')
            (root / 'payload.bin').write_bytes(b'0123456789')
            (root / 'outside').write_bytes(b'secret')
            (root / 'link').symlink_to(root / 'outside')
            binary = root / 'local-http'
            source = Path(__file__).parents[1] / 'runtime/local_http.c'
            subprocess.run(['gcc', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(source), '-o', str(binary)], check=True)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            server = subprocess.Popen([str(binary), str(root), '127.0.0.1', str(port)])
            try:
                url = f'http://127.0.0.1:{port}/'
                for _ in range(50):
                    try:
                        with urlopen(url + 'image.json', timeout=1) as response:
                            self.assertEqual(response.read(), b'{"ok":true}\n')
                            self.assertEqual(response.headers['Content-Length'], '12')
                        break
                    except OSError:
                        time.sleep(0.02)
                else:
                    self.fail('HTTP server did not start')
                with urlopen(Request(url + 'payload.bin', method='HEAD')) as response:
                    self.assertEqual(response.headers['Content-Length'], '10')
                    self.assertEqual(response.read(), b'')
                with urlopen(Request(url + 'payload.bin', headers={'Range': 'bytes=4-'})) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.read(), b'456789')
                for name in ('link', 'missing', '%2e%2e%2foutside'):
                    with self.subTest(name=name), self.assertRaises(HTTPError):
                        urlopen(url + name)
            finally:
                server.terminate()
                server.wait(timeout=5)
