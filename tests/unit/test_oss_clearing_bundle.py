import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    'clearing', Path(__file__).resolve().parents[2] / 'scripts/create-oss-clearing-bundle.py'
)
clearing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clearing)


class ClearingAttributionsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def archive(self, path):
        with tarfile.open(path, 'w:gz') as archive:
            for name, content in [('parted-3.6/AUTHORS', 'Alice <alice@example.org>\n'),
                                  ('parted-3.6/lib/COPYRIGHT', 'Copyright 2023 Bob\n'),
                                  ('perl-5.38.2/t/porting/copyright.t', 'Perl test script'),
                                  ('parted-3.6/main.c', 'not a notice document')]:
                data = content.encode()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            link = tarfile.TarInfo('parted-3.6/NOTICE')
            link.type = tarfile.SYMTYPE
            link.linkname = '/etc/passwd'
            archive.addfile(link)

    def test_nested_archiver_and_links(self):
        source = self.root / 'source'
        source.mkdir()
        self.archive(source / 'parted.tar.gz')
        material = clearing.read_source_attributions(source)
        self.assertEqual(len(material), 2)
        self.assertIn('source/parted.tar.gz/parted-3.6/AUTHORS', dict(material))

    def test_attribution_document_extensions(self):
        for name in ('AUTHORS', 'COPYRIGHT', 'AUTHORS.txt', 'contributors.MD',
                     'NOTICE.rst'):
            with self.subTest(name=name):
                self.assertTrue(clearing.is_attribution_file(name))
        for name in ('copyright.t', 'authors.py', 'license.c', 'NOTICE.txt.sh',
                     'COPYING.extra', 'AUTHORS-backup'):
            with self.subTest(name=name):
                self.assertFalse(clearing.is_attribution_file(name))

    def test_copyright_policy_and_evidence(self):
        for expression in ('GPL-3.0-only', 'GPLv2', 'LGPL-2.1-or-later',
                           'MIT', 'BSD-3-Clause', 'Apache-2.0',
                           'PD | GPL-2.0-only',
                           'GPL-2.0-only WITH Linux-syscall-note'):
            with self.subTest(expression=expression):
                self.assertEqual(clearing.copyright_license_policy(expression), (True, []))
        self.assertEqual(clearing.copyright_license_policy('CC0-1.0 | PD'), (False, []))
        self.assertEqual(clearing.copyright_license_policy('Custom-License'),
                         (False, ['Custom-License']))
        gpl = ('GNU GENERAL PUBLIC LICENSE\n'
               'Copyright (C) 2007 Free Software Foundation, Inc.\n'
               'Everyone is permitted to copy\n'
               'Copyright (C) <year> <name of author>')
        self.assertFalse(clearing.has_copyright_information([
            ('COPYING', gpl), ('AUTHORS', 'Alice <alice@example.org>')]))
        self.assertFalse(clearing.has_copyright_information([
            ('LICENSE', 'Copyright (c) 2024 <copyright holder>')]))
        self.assertTrue(clearing.has_copyright_information([
            ('COPYRIGHT', 'Copyright (C) 2023-2024 Alice Example')]))
        self.assertTrue(clearing.has_copyright_information([
            ('SPDX copyright statements', './main.c\nAlice Example')]))

    def test_copyright_holder_with_email(self):
        self.assertTrue(clearing.has_copyright_information([
            ('COPYING', 'Copyright (c) 2002-2019, Jouni Malinen <j@w1.fi> and contributors')]))
        self.assertTrue(clearing.has_copyright_information([
            ('LICENSE', 'Copyright (C) 2003 Alice Example <alice@example.org>')]))
        for placeholder in ('Copyright (c) 2024 <copyright holder>',
                            'Copyright (c) 2024 <name of author>',
                            'Copyright (c) 2024 [fullname]',
                            'Copyright 2024 [name of copyright owner]',
                            'Copyright (C) 2024 <OWNER>'):
            with self.subTest(placeholder=placeholder):
                self.assertFalse(clearing.has_copyright_information([
                    ('LICENSE', placeholder)]))

    def test_bare_c_symbol_copyright(self):
        zlib = 'Copyright notice:\n\n (C) 1995-2022 Jean-loup Gailly and Mark Adler\n'
        self.assertTrue(clearing.has_copyright_information([('LICENSE', zlib)]))
        self.assertTrue(clearing.has_copyright_information([
            ('NOTICE', '© 2020 Alice Example')]))
        self.assertTrue(clearing.has_copyright_information([
            ('NOTICE', ' * (c) 2021 Alice Example')]))
        # "(c)" glued to a word is not a copyright sign.
        self.assertFalse(clearing.has_copyright_information([
            ('NOTICE', 'see section 4(c) 2021 edition')]))

    def test_spdx_file_copyright_text(self):
        self.assertTrue(clearing.has_copyright_information([
            ('LICENSE', 'SPDX-FileCopyrightText: 2021 Alice Example')]))
        self.assertTrue(clearing.has_copyright_information([
            ('LICENSE', '# SPDX-FileCopyrightText: © 2021-2023 Alice Example')]))

    def test_license_tokens_keep_underscores(self):
        expression = 'Firmware-amlogic_vdec & Firmware-amphion_vpu & GPL-2.0-only'
        self.assertEqual(clearing.license_tokens(expression),
                         {'Firmware-amlogic_vdec', 'Firmware-amphion_vpu', 'GPL-2.0-only'})
        required, unknown = clearing.copyright_license_policy(expression)
        self.assertTrue(required)
        self.assertEqual(unknown, ['Firmware-amlogic_vdec', 'Firmware-amphion_vpu'])
        self.assertTrue(clearing.is_closed_license('CLOSED'))
        self.assertFalse(clearing.is_closed_license('CLOSED_extra'))

    def test_corresponding_source_license_names(self):
        for expression in ('GPLv2', 'GPLv3+', 'LGPLv2.1', 'LGPL-2.1-or-later',
                           'GPL-2.0-only', 'AGPL-3.0-only', 'GPL',
                           'MPL-2.0 | MIT', 'BSD-3-Clause & EPL-2.0',
                           'GPL-2.0-only WITH Linux-syscall-note'):
            with self.subTest(expression=expression):
                self.assertTrue(clearing.requires_corresponding_source(expression))
        for expression in ('MIT', 'BSD-3-Clause', 'Apache-2.0', 'CLOSED',
                           'Firmware-GPLish_blob', 'MIT WITH GPL-exception'):
            with self.subTest(expression=expression):
                self.assertFalse(clearing.requires_corresponding_source(expression))

    def test_legacy_gpl_name_without_source_prevents_preview(self):
        deploy = self.root / 'deploy'
        legal = deploy / 'licenses/target/ttynvt'
        legal.mkdir(parents=True)
        (legal / 'COPYRIGHT').write_text('Copyright 2024 Alice Example')
        manifest = deploy / 'licenses/target/image/license.manifest'
        manifest.parent.mkdir()
        manifest.write_text('PACKAGE NAME: ttynvt\nPACKAGE VERSION: 0.0.2\n'
                            'RECIPE NAME: ttynvt\nLICENSE: GPLv2\n')
        image = self.root / 'image.spdx.tar.zst'
        image.touch()
        with self.assertRaisesRegex(SystemExit, 'ttynvt:.*requires corresponding source'):
            clearing.make_preview(deploy, self.root / 'preview', image)

    def write_tar(self, path, members):
        with tarfile.open(path, 'w:gz') as archive:
            for name, data in members:
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

    def header_notices(self, material):
        return dict(material).get(clearing.SOURCE_HEADER_NOTICES, '').splitlines()

    def test_source_header_scan_in_archive(self):
        gpl = (b'GNU GENERAL PUBLIC LICENSE\n'
               b'Copyright (C) 1989, 1991 Free Software Foundation, Inc.\n'
               b'Everyone is permitted to copy and distribute verbatim copies\n')
        late = b'/* filler */\n' * 1000 + b'/* Copyright 2020 Too Late */\n'
        archive = self.root / 'wget.tar.gz'
        self.write_tar(archive, [
            ('wget-1.0/src/main.c',
             b'/* Copyright (C) 1996-2011, 2018-2023 Free Software Foundation,\n'
             b'   Inc.\n */\n#include <stdio.h>\n'),
            ('wget-1.0/src/http.c',
             b'/*\n * Copyright (c) 2010 Alice Example <alice@example.org>\n */\n'),
            ('wget-1.0/src/dup.c',
             b'// Copyright (c) 2010 Alice Example <alice@example.org>\n'),
            ('wget-1.0/lib/base64.py',
             b'# SPDX-FileCopyrightText: 2021 Bob Example\n'),
            ('wget-1.0/COPYING.GPLv2', gpl),
            ('wget-1.0/src/late.c', late),
            ('wget-1.0/doc/logo.png', b'\x89PNG\0Copyright 2020 Binary Corp\n'),
            ('wget-1.0/src/tmpl.c', b'/* Copyright (c) <year> <copyright holder> */\n'),
            ('wget-1.0/COPYRIGHT', b'Copyright 2019 Carol Example\n'),
        ])
        material = clearing.read_source_attributions(archive)
        self.assertCountEqual(self.header_notices(material), [
            'Copyright (c) 2010 Alice Example <alice@example.org>',
            'Copyright (C) 1996-2011, 2018-2023 Free Software Foundation,',
            'SPDX-FileCopyrightText: 2021 Bob Example',
        ])
        # Attribution documents stay verbatim and are not duplicated.
        self.assertIn('source/wget.tar.gz/wget-1.0/COPYRIGHT', dict(material))
        self.assertTrue(clearing.has_copyright_information(
            [(clearing.SOURCE_HEADER_NOTICES, '\n'.join(self.header_notices(material)))]))

    def test_source_header_scan_in_directory(self):
        source = self.root / 'go-source'
        (source / 'cmd').mkdir(parents=True)
        (source / 'cmd/main.go').write_text('// Copyright 2023 Ci4Rail GmbH\npackage main\n')
        self.write_tar(source / 'vendor.tar.gz', [
            ('vendor/x/y.go', b'// Copyright 2009 The Go Authors. All rights reserved.\n')])
        (source / 'binary').write_bytes(b'\0Copyright 2020 Binary Corp')
        self.assertCountEqual(self.header_notices(clearing.read_source_attributions(source)), [
            'Copyright 2023 Ci4Rail GmbH',
            'Copyright 2009 The Go Authors. All rights reserved.',
        ])

    def test_no_header_entry_without_notices(self):
        archive = self.root / 'plain.tar.gz'
        self.write_tar(archive, [('p/main.c', b'int main(void) { return isspace(c); }\n')])
        self.assertEqual(clearing.read_source_attributions(archive), [])

    def test_header_copyright_satisfies_preview(self):
        deploy = self.root / 'deploy'
        legal = deploy / 'licenses/target/example'
        legal.mkdir(parents=True)
        (legal / 'LICENSE').write_text('Copyright (c) <year> <copyright holders>\n')
        recipe_dir = deploy / 'spdx/recipes'
        recipe_dir.mkdir(parents=True)
        source = recipe_dir / 'recipe-example.tar.gz'
        self.write_tar(source, [('example-1.0/main.c',
                                 b'/* Copyright 2024 Alice Example */\n')])
        manifest = deploy / 'licenses/target/image/license.manifest'
        manifest.parent.mkdir()
        manifest.write_text('PACKAGE NAME: example\nPACKAGE VERSION: 1.0\n'
                            'RECIPE NAME: example\nLICENSE: MIT\n')
        image = self.root / 'image.spdx.tar.zst'
        image.touch()
        preview = self.root / 'preview'
        # Uncompressed and gzip archives are only picked up as archiver
        # output, so point the lookup at the fixture directly.
        original = clearing.find_source_archive
        clearing.find_source_archive = lambda *_args: source
        self.addCleanup(setattr, clearing, 'find_source_archive', original)
        clearing.make_preview(deploy, preview, image)
        page = (preview / 'components/example-1.0.html').read_text()
        self.assertIn(clearing.SOURCE_HEADER_NOTICES, page)
        self.assertIn('Copyright 2024 Alice Example', page)

    @unittest.skipUnless(shutil.which('zstd'), 'zstd is required for Yocto archives')
    def test_empty_spdx_archive_falls_back_to_archiver(self):
        # create-spdx writes an empty archive for the shared kernel source.
        deploy = self.root / 'deploy'
        recipe_dir = deploy / 'spdx/recipes'
        recipe_dir.mkdir(parents=True)
        empty = self.root / 'empty.tar'
        tarfile.open(empty, 'w').close()
        spdx_archive = recipe_dir / 'recipe-linux-toradex.tar.zst'
        subprocess.run(['zstd', '-q', str(empty), '-o', str(spdx_archive)], check=True)
        self.assertFalse(clearing.archive_has_files(spdx_archive))
        archiver = deploy / 'sources/aarch64-tdx-linux/linux-toradex-6.6.54+git-r0'
        archiver.mkdir(parents=True)
        self.write_tar(archiver / 'linux-toradex-6.6.54+git-r0-patched.tar.gz', [
            ('linux/kernel/fork.c', b'/* Copyright (C) 1991, 1992  Linus Torvalds */\n')])
        self.assertEqual(
            clearing.find_source_archive(deploy, 'linux-toradex', '6.6.54+git'), archiver)
        notices = self.header_notices(clearing.read_source_attributions(archiver))
        self.assertEqual(notices, ['Copyright (C) 1991, 1992 Linus Torvalds'])

    @unittest.skipUnless(shutil.which('zstd'), 'zstd is required for Yocto archives')
    def test_archive_has_files_stops_early(self):
        plain = self.root / 'big.tar'
        with tarfile.open(plain, 'w') as archive:
            for index in range(200):
                data = b'x' * 65536
                member = tarfile.TarInfo(f'big/{index}.c')
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        packed = self.root / 'big.tar.zst'
        subprocess.run(['zstd', '-q', str(plain), '-o', str(packed)], check=True)
        self.assertTrue(clearing.archive_has_files(packed))

    def test_missing_copyright_prevents_preview_and_bundle(self):
        deploy = self.root / 'deploy'
        legal = deploy / 'licenses/target/example'
        legal.mkdir(parents=True)
        (legal / 'LICENSE').write_text(
            'Copyright (c) <year> <copyright holders>\n'
            'The above copyright notice shall be included in all copies.')
        (legal / 'AUTHORS').write_text('Alice Example')
        manifest = deploy / 'licenses/target/image/license.manifest'
        manifest.parent.mkdir()
        manifest.write_text('PACKAGE NAME: example\nPACKAGE VERSION: 1.0\n'
                            'RECIPE NAME: example\nLICENSE: MIT\n')
        image = self.root / 'image.spdx.tar.zst'
        image.touch()
        preview = self.root / 'preview'
        bundle = self.root / 'bundle.tar.gz'
        with self.assertRaisesRegex(SystemExit, 'example:.*no component copyright'):
            clearing.make_preview(deploy, preview, image)
        with self.assertRaisesRegex(SystemExit, 'example:.*no component copyright'):
            clearing.make_bundle(deploy, bundle, image)
        self.assertFalse(preview.exists())
        self.assertFalse(bundle.exists())
        (legal / 'COPYRIGHT').write_text('Copyright 2024 Alice Example')
        clearing.make_preview(deploy, preview, image)
        self.assertTrue((preview / 'components/example-1.0.html').exists())

    @unittest.skipUnless(shutil.which('zstd'), 'zstd is required for Yocto archives')
    def test_preview_and_bundle_include_source_notices_and_spdx(self):
        deploy = self.root / 'deploy'
        recipe_dir = deploy / 'spdx/recipes'
        recipe_dir.mkdir(parents=True)
        tar = self.root / 'source.tar.gz'
        self.archive(tar)
        # Convert to an uncompressed tar, then the format used by Yocto.
        plain = self.root / 'source.tar'
        with tarfile.open(tar) as src, tarfile.open(plain, 'w') as dst:
            for member in src:
                dst.addfile(member, src.extractfile(member) if member.isfile() else None)
        subprocess.run(['zstd', '-q', str(plain), '-o', str(recipe_dir / 'recipe-parted.tar.zst')], check=True)
        (recipe_dir / 'recipe-parted.spdx.json').write_text(json.dumps({
            'files': [{'fileName': './main.c', 'copyrightText': 'Copyright 2024 Carol'},
                      {'copyrightText': 'NOASSERTION'}]}))
        legal = deploy / 'licenses/target/parted'
        legal.mkdir(parents=True)
        (legal / 'COPYING').write_text('GPL license text')
        manifest = deploy / 'licenses/target/image/license.manifest'
        manifest.parent.mkdir()
        manifest.write_text('PACKAGE NAME: parted\nPACKAGE VERSION: 3.6\nRECIPE NAME: parted\nLICENSE: GPL-3.0-only\n')
        image = self.root / 'image.spdx.tar.zst'
        image.write_bytes(b'fixture SBOM')
        preview = self.root / 'preview'
        clearing.make_preview(deploy, preview, image)
        page = (preview / 'components/parted-3.6.html').read_text()
        self.assertIn('Alice &lt;alice@example.org&gt;', page)
        self.assertIn('Copyright 2024 Carol', page)
        self.assertIn('source/recipe-parted.tar.zst/parted-3.6/AUTHORS', page)
        self.assertIn('AUTHORS', (preview / 'components.csv').read_text())
        self.assertFalse((preview / 'sources').exists())
        bundle = self.root / 'bundle.tar.gz'
        clearing.make_bundle(deploy, bundle, image)
        with tarfile.open(bundle) as archive:
            page = archive.extractfile('image-oss-clearing/components/parted-3.6.html').read().decode()
            self.assertIn('Alice &lt;alice@example.org&gt;', page)
            self.assertIn('Copyright 2023 Bob', page)
            self.assertIn('Copyright 2024 Carol', page)


if __name__ == '__main__':
    unittest.main()
