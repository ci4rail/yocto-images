import csv
import importlib.util
import json
import tarfile
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "create-oss-clearing-bundle.py"
SPEC = importlib.util.spec_from_file_location("oss_clearing", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ClearingBundleTest(unittest.TestCase):
    def test_creates_review_preview_without_copying_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            deploy = Path(temporary) / "deploy"
            image_dir = deploy / "images" / "machine"
            license_dir = deploy / "licenses" / "arm64" / "demo"
            manifest_dir = deploy / "licenses" / "machine" / "test-image"
            source_dir = deploy / "spdx" / "recipes"
            for directory in (image_dir, license_dir, manifest_dir, source_dir):
                directory.mkdir(parents=True)
            image = image_dir / "test-image.spdx.tar.zst"
            image.write_bytes(b"sbom")
            (manifest_dir / "license.manifest").write_text(
                "PACKAGE NAME: demo\nPACKAGE VERSION: 1\n"
                "RECIPE NAME: demo\nLICENSE: GPL-2.0-only\n\n",
                encoding="utf-8",
            )
            (license_dir / "COPYING").write_text("GPL text", encoding="utf-8")
            (source_dir / "recipe-demo.tar.zst").write_bytes(b"source")

            preview = Path(temporary) / "preview"
            self.assertEqual(MODULE.make_preview(deploy, preview, image), 1)
            disclosure = (preview / "THIRD_PARTY_SOFTWARE.html").read_text()
            self.assertIn("review preview", disclosure)
            self.assertIn("components/demo-1.html", disclosure)
            detail = (preview / "components" / "demo-1.html").read_text()
            self.assertIn("sources/demo/1/recipe-demo.tar.zst", detail)
            self.assertIn("GPL text", detail)
            self.assertFalse((preview / "sources").exists())
            self.assertFalse((preview / "sbom").exists())

    def test_creates_offline_component_addressable_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            deploy = Path(temporary) / "deploy"
            image_dir = deploy / "images" / "machine"
            license_dir = deploy / "licenses" / "arm64" / "demo"
            # This is the standard OE-Core SPDX_ARCHIVE_SOURCES deploy layout.
            source_dir = deploy / "spdx" / "recipes"
            for directory in (image_dir, license_dir, source_dir):
                directory.mkdir(parents=True)

            image = image_dir / "test-image.spdx.tar.zst"
            image.write_bytes(b"sbom")
            manifest_dir = deploy / "licenses" / "machine" / "test-image"
            manifest_dir.mkdir(parents=True)
            (manifest_dir / "license.manifest").write_text(
                "PACKAGE NAME: demo-bin\n"
                "PACKAGE VERSION: 1.2.3\n"
                "RECIPE NAME: demo\n"
                "LICENSE: MIT\n\n",
                encoding="utf-8",
            )
            (license_dir / "generic_MIT").write_text("MIT full text", encoding="utf-8")
            (license_dir / "NOTICE").write_text(
                "Copyright 2026 Example\nAcknowledgement", encoding="utf-8"
            )
            (source_dir / "recipe-demo.tar.zst").write_bytes(b"source")
            (source_dir / "recipe-demo.spdx.json").write_text(
                json.dumps(
                    {
                        "packages": [
                            {
                                "SPDXID": "SPDXRef-Download-demo-1",
                                "name": "demo-source-1",
                                "downloadLocation": (
                                    "git+https://example.invalid/demo.git@"
                                    "0123456789abcdef0123456789abcdef01234567"
                                ),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            output = Path(temporary) / "delivery.tar.gz"
            count = MODULE.make_bundle(deploy, output, image)
            self.assertEqual(count, 1)

            extract = Path(temporary) / "extract"
            with tarfile.open(output) as archive:
                archive.extractall(extract, filter="data")
            root = next(extract.iterdir())
            disclosure = (root / "THIRD_PARTY_SOFTWARE.html").read_text()
            self.assertIn("demo-bin", disclosure)
            self.assertIn("components/demo-1.2.3.html", disclosure)
            detail = (root / "components" / "demo-1.2.3.html").read_text()
            self.assertIn("MIT full text", detail)
            self.assertIn("Copyright 2026 Example", detail)
            self.assertIn(
                "0123456789abcdef0123456789abcdef01234567", detail
            )
            self.assertIn("sources/demo/1.2.3/recipe-demo.tar.zst", detail)
            with (root / "components.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["applicable_license"], "MIT")
            self.assertEqual(
                rows[0]["source_revision"],
                "0123456789abcdef0123456789abcdef01234567",
            )

    def test_permissive_component_does_not_require_source_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            deploy = Path(temporary) / "deploy"
            image_dir = deploy / "images" / "machine"
            license_dir = deploy / "licenses" / "arm64" / "demo"
            manifest_dir = deploy / "licenses" / "machine" / "test-image"
            for directory in (image_dir, license_dir, manifest_dir):
                directory.mkdir(parents=True)
            image = image_dir / "test-image.spdx.tar.zst"
            image.write_bytes(b"sbom")
            (manifest_dir / "license.manifest").write_text(
                "PACKAGE NAME: demo\nPACKAGE VERSION: 1\n"
                "RECIPE NAME: demo\nLICENSE: BSD-3-Clause\n\n",
                encoding="utf-8",
            )
            (license_dir / "LICENSE").write_text("BSD text", encoding="utf-8")

            output = Path(temporary) / "delivery.tar.gz"
            self.assertEqual(MODULE.make_bundle(deploy, output, image), 1)

    def test_copyleft_component_requires_source_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            deploy = Path(temporary) / "deploy"
            image_dir = deploy / "images" / "machine"
            license_dir = deploy / "licenses" / "arm64" / "demo"
            manifest_dir = deploy / "licenses" / "machine" / "test-image"
            for directory in (image_dir, license_dir, manifest_dir):
                directory.mkdir(parents=True)
            image = image_dir / "test-image.spdx.tar.zst"
            image.write_bytes(b"sbom")
            (manifest_dir / "license.manifest").write_text(
                "PACKAGE NAME: demo\nPACKAGE VERSION: 1\n"
                "RECIPE NAME: demo\nLICENSE: GPL-2.0-only\n\n",
                encoding="utf-8",
            )
            (license_dir / "COPYING").write_text("GPL text", encoding="utf-8")

            with self.assertRaises(SystemExit) as context:
                MODULE.make_bundle(deploy, Path(temporary) / "delivery.tar.gz", image)
            self.assertIn("requires corresponding source", str(context.exception))


if __name__ == "__main__":
    unittest.main()
