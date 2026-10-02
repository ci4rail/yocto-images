"""Security checks for the recovery CI artifact path, policy and FIT verification."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ci'))
import recovery


class DestinationTests(unittest.TestCase):
    def test_missing_configuration_fails_before_build(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, 'secret'):
            recovery.validate_environment('staging')
        with self.assertRaisesRegex(ValueError, 'no reviewed signing configuration'):
            recovery.settings('cpu01plus', 'production')

    def test_production_uses_azure_identity_without_fingerprint_variable(self):
        with patch.dict(os.environ, {
                'MINIO_ACCESS_KEY': 'fixture', 'MINIO_SECRET_KEY': 'fixture',
                'MINIO_BUCKET': 'fixture-bucket',
                'RECOVERY_ROOT_PASSWORD_HASH': '$6$salt$' + 'a' * 86,
                'AZURE_CLIENT_ID': 'fixture-client', 'AZURE_TENANT_ID': 'fixture-tenant',
        }, clear=True):
            recovery.validate_environment('production')
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                _, _, signing = recovery.settings('cpu01', 'production')
                def azure_tools(*args):
                    if 'akv-fetch-certificate.py' in str(args[1]):
                        (work / 'fit.der').write_bytes(b'Azure public certificate fixture')
                    elif args[0] == 'openssl':
                        (work / 'fit.crt').write_bytes(b'PEM certificate fixture')
                with patch.object(recovery, 'run', side_effect=azure_tools) as command, \
                        patch.object(recovery, 'trust_tree', return_value='fingerprint') as trust:
                    arguments, fingerprint = recovery.signing_arguments(signing, 'production', work)
                trust.assert_called_once_with(work / 'fit.crt', signing['keyname'],
                                              signing['algorithm'], work / 'trust.dtb')
                command.assert_any_call('python3', recovery.AKV / 'akv-fetch-certificate.py',
                                        '--key-id', signing['key_id'], '--output', work / 'fit.der')
                self.assertIn('pkcs11', arguments)
                self.assertEqual(fingerprint, 'fingerprint')

    def test_public_policy_rejected_even_when_conditional(self):
        for principal in ('*', {'AWS': '*'}, {'AWS': ['*']}, {'AWS': ['user', '*']}):
            with self.subTest(principal=principal), self.assertRaises(ValueError):
                recovery.assert_private_policy({'Statement': [{
                    'Effect': 'Allow', 'Principal': principal, 'Action': 's3:GetObject',
                    'Condition': {'IpAddress': {'aws:SourceIp': '192.0.2.0/24'}},
                }]})
        with self.assertRaises(ValueError):
            recovery.assert_private_policy({'Statement': {'Effect': 'Allow', 'NotPrincipal': 'user'}})
        recovery.assert_private_policy({})
        recovery.assert_private_policy({'Statement': [
            {'Effect': 'Deny', 'Principal': '*'},
            {'Effect': 'Allow', 'Principal': {'AWS': 'service-user'}},
        ]})

    def test_destination_cannot_escape_namespace_or_inject_outputs(self):
        args = ['recovery', 'cpu01', 'production', '1.0', 'a' * 40, '123', '1']
        self.assertTrue(recovery.object_prefix(*args).endswith('/123-1'))
        for value in ('../public', '.', '..', 'a\nsha256=bad', '/absolute', 'a/b'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                recovery.object_prefix(value, *args[1:])
        second_attempt = recovery.object_prefix(*args[:-1], '2')
        self.assertNotEqual(recovery.object_prefix(*args), second_attempt)


@unittest.skipUnless(importlib.util.find_spec('boto3'), 'CI S3 client required')
class UploadTests(unittest.TestCase):
    def test_policy_failure_never_uploads(self):
        from botocore.exceptions import ClientError
        policies = [
            {'Statement': [{'Effect': 'Allow', 'Principal': '*'}]},
            ClientError({'Error': {'Code': 'AccessDenied'}}, 'GetBucketPolicy'),
        ]
        for policy in policies:
            with self.subTest(policy=policy):
                client = Mock()
                if isinstance(policy, Exception):
                    client.get_bucket_policy.side_effect = policy
                else:
                    client.get_bucket_policy.return_value = {'Policy': json.dumps(policy)}
                with patch('boto3.client', return_value=client), patch.dict(os.environ, {
                        'MINIO_ACCESS_KEY': 'fixture', 'MINIO_SECRET_KEY': 'fixture',
                        'MINIO_BUCKET': 'fixture-bucket'}):
                    with self.assertRaises((ValueError, ClientError)):
                        recovery.upload(SimpleNamespace(work=Path('/unused'), platform='cpu01', mode='staging'))
                client.upload_file.assert_not_called()


@unittest.skipUnless(shutil.which('fit_check_sign'), 'CI U-Boot verification tools required')
class TrustTests(unittest.TestCase):
    def test_verification_rejects_wrong_key_and_modified_image(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        for bits in (2048, 3072):
            with self.subTest(bits=bits), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for name in ('dev', 'wrong'):
                    subprocess.run(['openssl', 'req', '-new', '-x509', '-newkey', f'rsa:{bits}',
                                    '-nodes', '-subj', '/CN=test/', '-keyout', str(root / f'{name}.key'),
                                    '-out', str(root / f'{name}.crt'), '-days', '1'],
                                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                key = x509.load_pem_x509_certificate((root / 'dev.crt').read_bytes()).public_key()
                fingerprint = hashlib.sha256(key.public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
                algorithm = f'sha256,rsa{bits}'
                self.assertEqual(recovery.trust_tree(
                    root / 'dev.crt', 'dev', algorithm, root / 'trust.dtb'), fingerprint)
                recovery.trust_tree(root / 'wrong.crt', 'dev', algorithm, root / 'wrong.dtb')
                (root / 'kernel').write_bytes(b'kernel CI fixture')
                (root / 'ramdisk').write_bytes(b'ramdisk CI fixture')
                subprocess.run(['dtc', '-O', 'dtb', '-o', str(root / 'board.dtb')],
                               input='/dts-v1/; / {};', text=True, check=True)
                fit = root / 'recovery.itb'
                recovery.build.make_fit(root / 'kernel', root / 'ramdisk', root / 'board.dtb',
                                        0x48200000, 'none', fit, str(root), algorithm=algorithm)
                def verify(trust):
                    return subprocess.run(['fit_check_sign', '-f', str(fit), '-k', str(trust),
                                           '-c', 'recovery'], stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL).returncode
                self.assertEqual(verify(root / 'trust.dtb'), 0)
                self.assertNotEqual(verify(root / 'wrong.dtb'), 0)
                data = fit.read_bytes()
                self.assertIn(b'kernel CI fixture', data)
                fit.write_bytes(data.replace(b'kernel CI fixture', b'broken CI fixture'))
                self.assertNotEqual(verify(root / 'trust.dtb'), 0)
