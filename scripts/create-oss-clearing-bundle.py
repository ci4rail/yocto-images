#!/usr/bin/env python3
"""Create an offline OSS/third-party clearing bundle from Yocto deploy output."""

import argparse
import csv
import html
import json
import re
import shutil
import sys
import tarfile
import tempfile
from collections import OrderedDict
from pathlib import Path


MANIFEST_FIELDS = ("PACKAGE NAME", "PACKAGE VERSION", "RECIPE NAME", "LICENSE")
NON_OSS_LICENSES = {"CLOSED", "NONE", "NOASSERTION", "Proprietary"}
SOURCE_REQUIRED_LICENSE_MARKERS = (
    "AGPL-",
    "CDDL-",
    "CPAL-",
    "EPL-",
    "EUPL-",
    "GPL-",
    "LGPL-",
    "MPL-",
    "OSL-",
)


def die(message):
    raise SystemExit(f"error: {message}")


def parse_manifest(path):
    components = []
    current = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw_line.strip():
            if current:
                if all(field in current for field in MANIFEST_FIELDS):
                    components.append(current)
                current = {}
            continue
        key, separator, value = raw_line.partition(": ")
        if separator:
            current[key] = value
    if current and all(field in current for field in MANIFEST_FIELDS):
        components.append(current)
    return components


def safe_name(value):
    value = re.sub(r"[^A-Za-z0-9._+-]+", "_", value.strip())
    return value.strip("._") or "unknown"


def find_latest_image(deploy_dir):
    images = list((deploy_dir / "images").glob("*/*.spdx.tar.zst"))
    images = [path for path in images if not path.is_symlink()]
    if not images:
        die(f"no image SPDX archive found below {deploy_dir / 'images'}")
    return max(images, key=lambda path: path.stat().st_mtime)


def find_manifest(deploy_dir, image_stem):
    exact = list((deploy_dir / "licenses").glob(f"*/*{image_stem}*/license.manifest"))
    candidates = exact or list((deploy_dir / "licenses").glob("*/*/license.manifest"))
    if not candidates:
        die(f"no license.manifest found below {deploy_dir / 'licenses'}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def find_recipe_legal_dir(deploy_dir, recipe):
    candidates = [
        path
        for path in (deploy_dir / "licenses").glob(f"*/*/{recipe}")
        if path.is_dir() and "native" not in path.parts
    ]
    if not candidates:
        candidates = [
            path
            for path in (deploy_dir / "licenses").glob(f"*/{recipe}")
            if path.is_dir() and "native" not in path.parts
        ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def source_recipe_candidates(recipe, version):
    yield recipe
    # OE deliberately archives these shared source trees only once.
    if recipe in {"libgcc", "gcc-runtime"}:
        yield f"gcc-source-{version.split('+', 1)[0]}"
    elif recipe == "glibc-locale":
        yield "glibc"


def find_source_archive(deploy_dir, recipe, version):
    candidates = []
    source_recipes = tuple(source_recipe_candidates(recipe, version))
    for source_recipe in source_recipes:
        # OE-Core normally deploys archives directly below spdx/recipes.
        # Some configurations add an architecture directory, so accept both.
        for recipe_dir in (
            deploy_dir / "spdx" / "recipes",
            *((deploy_dir / "spdx").glob("*/recipes")),
        ):
            candidates += list(recipe_dir.glob(f"recipe-{source_recipe}.tar.zst"))
            candidates += list(recipe_dir.glob(f"{source_recipe}.tar.zst"))
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime)

    # OE's create-spdx class deliberately skips shared GCC source.  The
    # archiver class deploys that source, its recipe and patches below
    # DEPLOY_DIR/sources instead.
    archiver_candidates = []
    sources_dir = deploy_dir / "sources"
    if sources_dir.exists():
        for source_recipe in source_recipes:
            archiver_candidates += [
                path
                for path in sources_dir.glob(f"**/{source_recipe}-*")
                if path.is_dir()
            ]
    return (
        max(archiver_candidates, key=lambda path: path.stat().st_mtime)
        if archiver_candidates
        else None
    )


def find_recipe_spdx(deploy_dir, recipe):
    candidates = list(
        (deploy_dir / "spdx").glob(f"*/recipes/recipe-{recipe}.spdx.json")
    )
    candidates += list(
        (deploy_dir / "spdx" / "recipes").glob(f"recipe-{recipe}.spdx.json")
    )
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def read_source_revisions(deploy_dir, recipe):
    recipe_spdx = find_recipe_spdx(deploy_dir, recipe)
    if not recipe_spdx:
        return []
    document = json.loads(recipe_spdx.read_text(encoding="utf-8"))
    revisions = []
    for package in document.get("packages", []):
        if not package.get("SPDXID", "").startswith("SPDXRef-Download-"):
            continue
        location = package.get("downloadLocation", "")
        match = re.search(r"@([0-9a-fA-F]{40,64})$", location)
        if not match:
            continue
        entry = (package.get("name", "source"), match.group(1).lower())
        if entry not in revisions:
            revisions.append(entry)
    return revisions


def copy_source_artifact(source, destination_dir):
    destination_dir.mkdir(parents=True)
    if source.is_file():
        destination = destination_dir / source.name
        shutil.copy2(source, destination)
        return destination

    destination = destination_dir / f"{safe_name(source.name)}.tar.gz"
    with tarfile.open(destination, "w:gz") as archive:
        archive.add(source, arcname=source.name)
    return destination


def read_legal_material(directory):
    material = []
    if directory:
        for path in sorted(directory.iterdir()):
            if path.is_file() and not path.is_symlink() and path.name != "recipeinfo":
                material.append((path.name, path.read_text(encoding="utf-8", errors="replace")))
    return material


def is_closed_license(expression):
    tokens = set(re.findall(r"[A-Za-z0-9.+-]+", expression))
    return bool(tokens) and tokens <= NON_OSS_LICENSES


def requires_corresponding_source(expression):
    return any(marker in expression for marker in SOURCE_REQUIRED_LICENSE_MARKERS)


HTML_STYLE = """
body{font:15px/1.45 sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem}
table{border-collapse:collapse;width:100%}th,td{border:1px solid #bbb;padding:.4rem;text-align:left}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:1rem}
dt{font-weight:bold}dd{margin-bottom:.35rem}section{border-top:2px solid #555;margin-top:2rem}
"""


def component_page_names(recipe_versions):
    return {
        recipe: f"{safe_name(recipe)}-{safe_name(version)}.html"
        for recipe, version in recipe_versions.items()
    }


def source_revision_text(revisions):
    if not revisions:
        return "Not recorded (non-Git source or unavailable SPDX revision metadata)"
    if len(revisions) == 1:
        return revisions[0][1]
    return "; ".join(f"{name}: {revision}" for name, revision in revisions)


def write_documentation(
    root,
    title,
    components,
    legal_by_recipe,
    recipe_versions,
    source_by_recipe,
    revisions_by_recipe,
    preview=False,
):
    details_dir = root / "components"
    details_dir.mkdir(exist_ok=True)
    page_names = component_page_names(recipe_versions)
    rows = []
    for component in components:
        package = component["PACKAGE NAME"]
        version = component["PACKAGE VERSION"]
        recipe = component["RECIPE NAME"]
        license_expression = component["LICENSE"]
        detail_link = f"components/{page_names[recipe]}"
        rows.append(
            "<tr>"
            f"<td><a href=\"{html.escape(detail_link)}\">"
            f"{html.escape(package)}</a></td>"
            f"<td>{html.escape(version)}</td>"
            f"<td><a href=\"{html.escape(detail_link)}\">"
            f"{html.escape(recipe)}</a></td>"
            f"<td>{html.escape(license_expression)}</td>"
            "</tr>"
        )

    delivery_note = (
        "This is a review preview. Source paths show where corresponding source "
        "will appear in the final clearing bundle; the source archives and SBOM "
        "are intentionally not copied into this preview."
        if preview
        else
        "All referenced source, SBOM, license, copyright and acknowledgement "
        "material is included in the same offline delivery archive."
    )
    index_document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(title)} — Third-party software documentation</title>
<style>{HTML_STYLE}</style></head><body>
<h1>{html.escape(title)} — Third-party software documentation</h1>
<p>This document applies to the exact image identified above. It is generated from
the package set and legal files used by the Yocto build. {html.escape(delivery_note)}
This document is not confidential and may be disclosed to third parties.</p>
<p>Each installed package links to an offline component detail page containing its
license texts, copyright notices, acknowledgements and source information.</p>
<h2>Installed package inventory</h2>
<table><thead><tr><th>Component/package</th><th>Version</th><th>Recipe</th>
<th>Applicable license (SPDX expression)</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>"""
    (root / "THIRD_PARTY_SOFTWARE.html").write_text(
        index_document, encoding="utf-8"
    )

    for recipe, recipe_version in recipe_versions.items():
        recipe_components = [
            item for item in components if item["RECIPE NAME"] == recipe
        ]
        licenses = sorted({item["LICENSE"] for item in recipe_components})
        package_rows = "".join(
            "<tr>"
            f"<td>{html.escape(item['PACKAGE NAME'])}</td>"
            f"<td>{html.escape(item['PACKAGE VERSION'])}</td>"
            f"<td>{html.escape(item['LICENSE'])}</td>"
            "</tr>"
            for item in recipe_components
        )
        legal = legal_by_recipe[recipe]
        legal_html = "".join(
            f"<h4>{html.escape(name)}</h4><pre>{html.escape(text)}</pre>"
            for name, text in legal
        )
        source = source_by_recipe.get(recipe)
        if source:
            source_text = source.as_posix()
        elif all(is_closed_license(expression) for expression in licenses):
            source_text = "Not applicable (closed/non-OSS component)"
        else:
            source_text = "Not required by the declared license; no source archive included"
        detail_document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(recipe)} {html.escape(recipe_version)} — component details</title>
<style>{HTML_STYLE}</style></head><body>
<p><a href="../THIRD_PARTY_SOFTWARE.html">Back to installed package inventory</a></p>
<h1>{html.escape(recipe)} {html.escape(recipe_version)}</h1>
<dl>
<dt>Upstream/build component</dt><dd>{html.escape(recipe)}</dd>
<dt>Recipe version</dt><dd>{html.escape(recipe_version)}</dd>
<dt>Source revision(s)</dt>
<dd>{html.escape(source_revision_text(revisions_by_recipe[recipe]))}</dd>
<dt>Declared license(s)</dt><dd>{html.escape(', '.join(licenses))}</dd>
<dt>Full license text</dt><dd>Included verbatim in the legal material below.</dd>
<dt>Copyright notices</dt><dd>Included verbatim in the legal material below.</dd>
<dt>Acknowledgements and additional mandatory information</dt>
<dd>Included verbatim in the legal material below.</dd>
<dt>Corresponding source in this delivery</dt><dd>{html.escape(source_text)}</dd>
</dl>
<h2>Installed packages produced by this component</h2>
<table><thead><tr><th>Installed package</th><th>Version</th>
<th>Applicable license (SPDX expression)</th></tr></thead>
<tbody>{package_rows}</tbody></table>
<h2>License texts, copyright notices and acknowledgements</h2>
{legal_html}
</body></html>"""
        (details_dir / page_names[recipe]).write_text(
            detail_document, encoding="utf-8"
        )


def collect_inventory(deploy_dir, image_spdx=None):
    image_spdx = image_spdx or find_latest_image(deploy_dir)
    image_stem = image_spdx.name.removesuffix(".spdx.tar.zst")
    manifest = find_manifest(deploy_dir, image_stem)
    components = parse_manifest(manifest)
    if not components:
        die(f"no components parsed from {manifest}")

    legal_by_recipe = {}
    source_artifacts = {}
    revisions_by_recipe = {}
    errors = []
    recipe_versions = OrderedDict()
    for component in components:
        recipe = component["RECIPE NAME"]
        recipe_versions.setdefault(recipe, component["PACKAGE VERSION"])
        if recipe not in legal_by_recipe:
            legal_by_recipe[recipe] = read_legal_material(
                find_recipe_legal_dir(deploy_dir, recipe)
            )
        if not legal_by_recipe[recipe]:
            errors.append(f"{recipe}: no collected legal material")
        if recipe not in revisions_by_recipe:
            revisions_by_recipe[recipe] = read_source_revisions(deploy_dir, recipe)

    for recipe, version in recipe_versions.items():
        component_licenses = {
            item["LICENSE"] for item in components if item["RECIPE NAME"] == recipe
        }
        is_closed = component_licenses and all(
            is_closed_license(license_name) for license_name in component_licenses
        )
        if is_closed:
            continue
        artifact = find_source_archive(deploy_dir, recipe, version)
        if not artifact:
            if any(
                requires_corresponding_source(expression)
                for expression in component_licenses
            ):
                errors.append(
                    f"{recipe}: declared license requires corresponding source, "
                    "but no archive was found"
                )
            continue
        source_artifacts[recipe] = artifact

    if errors:
        die("clearing bundle is incomplete:\n  " + "\n  ".join(sorted(set(errors))))
    return (
        image_spdx,
        image_stem,
        manifest,
        components,
        legal_by_recipe,
        recipe_versions,
        source_artifacts,
        revisions_by_recipe,
    )


def planned_source_paths(recipe_versions, source_artifacts):
    result = {}
    for recipe, artifact in source_artifacts.items():
        filename = (
            artifact.name
            if artifact.is_file()
            else f"{safe_name(artifact.name)}.tar.gz"
        )
        result[recipe] = (
            Path("sources")
            / safe_name(recipe)
            / safe_name(recipe_versions[recipe])
            / filename
        )
    return result


def write_csv(
    path, components, legal_by_recipe, source_by_recipe, revisions_by_recipe
):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "component_name",
                "version",
                "recipe",
                "source_revision",
                "applicable_license",
                "legal_material_files",
                "source_archive",
            ]
        )
        for component in components:
            recipe = component["RECIPE NAME"]
            writer.writerow(
                [
                    component["PACKAGE NAME"],
                    component["PACKAGE VERSION"],
                    recipe,
                    source_revision_text(revisions_by_recipe[recipe]),
                    component["LICENSE"],
                    ";".join(name for name, _text in legal_by_recipe[recipe]),
                    source_by_recipe.get(recipe, ""),
                ]
            )


def make_preview(deploy_dir, preview_dir, image_spdx=None):
    (
        _image_spdx,
        image_stem,
        manifest,
        components,
        legal_by_recipe,
        recipe_versions,
        source_artifacts,
        revisions_by_recipe,
    ) = collect_inventory(deploy_dir, image_spdx)
    source_by_recipe = planned_source_paths(recipe_versions, source_artifacts)
    preview_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest, preview_dir / "yocto-license.manifest")
    write_csv(
        preview_dir / "components.csv",
        components,
        legal_by_recipe,
        source_by_recipe,
        revisions_by_recipe,
    )
    write_documentation(
        preview_dir,
        image_stem,
        components,
        legal_by_recipe,
        recipe_versions,
        source_by_recipe,
        revisions_by_recipe,
        preview=True,
    )
    (preview_dir / "README.txt").write_text(
        "OSS CLEARING REVIEW PREVIEW\n\n"
        "Open THIRD_PARTY_SOFTWARE.html for the package inventory. Its local links\n"
        "open recipe/version detail pages below components/.\n"
        "This preview validates required legal material and corresponding-source\n"
        "availability, but deliberately does not copy the SBOM or source archives.\n"
        "The source paths shown are their planned locations in the final bundle.\n",
        encoding="utf-8",
    )
    return len(components)


def make_bundle(deploy_dir, output, image_spdx=None):
    (
        image_spdx,
        image_stem,
        manifest,
        components,
        legal_by_recipe,
        recipe_versions,
        source_artifacts,
        revisions_by_recipe,
    ) = collect_inventory(deploy_dir, image_spdx)

    with tempfile.TemporaryDirectory(prefix="oss-clearing-") as temporary:
        root = Path(temporary) / f"{safe_name(image_stem)}-oss-clearing"
        (root / "sbom").mkdir(parents=True)
        (root / "sources").mkdir()
        shutil.copy2(image_spdx, root / "sbom" / image_spdx.name)
        shutil.copy2(manifest, root / "yocto-license.manifest")

        source_by_recipe = {}
        for recipe, artifact in source_artifacts.items():
            destination = copy_source_artifact(
                artifact,
                root
                / "sources"
                / safe_name(recipe)
                / safe_name(recipe_versions[recipe]),
            )
            source_by_recipe[recipe] = destination.relative_to(root)

        write_csv(
            root / "components.csv",
            components,
            legal_by_recipe,
            source_by_recipe,
            revisions_by_recipe,
        )
        write_documentation(
            root,
            image_stem,
            components,
            legal_by_recipe,
            recipe_versions,
            source_by_recipe,
            revisions_by_recipe,
        )
        (root / "README.txt").write_text(
            "OSS CLEARING DELIVERY\n\n"
            "Open THIRD_PARTY_SOFTWARE.html for the package inventory. Its local\n"
            "links open recipe/version detail pages below components/.\n"
            "components.csv is the machine-readable inventory.\n"
            "sbom contains the SPDX 2.2 software bill of materials.\n"
            "sources contains unpacked-and-patched corresponding source archives,\n"
            "structured by build recipe and version.\n\n"
            "No Internet access is required to use this delivery.\n"
            "This delivery is not confidential and may be disclosed to third parties.\n",
            encoding="utf-8",
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(output, "w:gz") as archive:
            archive.add(root, arcname=root.name)
    return len(components)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deploy-dir", required=True, type=Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--preview-dir", type=Path)
    parser.add_argument("--image-spdx", type=Path)
    args = parser.parse_args()
    image_spdx = args.image_spdx.resolve() if args.image_spdx else None
    if args.preview_dir:
        count = make_preview(
            args.deploy_dir.resolve(), args.preview_dir.resolve(), image_spdx
        )
        print(
            f"Created review preview {args.preview_dir} "
            f"with {count} installed package entries"
        )
    else:
        count = make_bundle(args.deploy_dir.resolve(), args.output.resolve(), image_spdx)
        print(f"Created {args.output} with {count} installed package entries")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        die(str(error))
