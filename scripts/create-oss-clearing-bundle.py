#!/usr/bin/env python3
"""Create an offline OSS/third-party clearing bundle from Yocto deploy output.

Include deployed license material, conventional upstream attribution documents
from corresponding-source archives, copyright statements from the first
SOURCE_HEADER_BYTES of every other source file, and recipe SPDX copyright
statements.
This collection does not infer copyright ownership from contributor lists or
guarantee that every source-level notice has been identified. Reading Yocto's
.tar.zst source archives requires the zstd executable on PATH.

Both output modes reject missing component copyright evidence for licenses in
the conservative notice-preservation policy below. Unknown license IDs require
policy review. Passing this presence check is not a full compliance audit.
"""

import argparse
import csv
import html
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path


# Copyright notices normally sit at the top of a source file.
SOURCE_HEADER_BYTES = 8192
SOURCE_HEADER_NOTICES = "Source file copyright notices"
MANIFEST_FIELDS = ("PACKAGE NAME", "PACKAGE VERSION", "RECIPE NAME", "LICENSE")
NON_OSS_LICENSES = {"CLOSED", "NONE", "NOASSERTION", "Proprietary"}
# Matched against single license tokens. Accept legacy OE names such as
# GPLv2 and LGPLv2.1 as well as SPDX identifiers.
SOURCE_REQUIRED_LICENSE = re.compile(
    r"(?:A?GPL|LGPL)(?:$|-|v|\d)|(?:CDDL|CPAL|EPL|EUPL|MPL|OSL)-"
)
# Yocto license names may contain underscores, e.g. Firmware-amlogic_vdec.
LICENSE_TOKEN = re.compile(r"[A-Za-z0-9._+-]+")

# Clearing policy: preserve notices for these license families. For alternatives
# (OR / |), require notices if any declared branch requires them; the manifest
# does not record which alternative the distributor selected.
# Notice-preservation references:
# https://www.gnu.org/licenses/gpl-3.0.html (sections 4 and 6)
# https://opensource.org/license/mit
COPYRIGHT_REQUIRED_LICENSE = re.compile(
    r"(?:A?GPL|LGPL)(?:-|v|\d)|(?:BSD|Apache|MPL|CDDL|CPL|EPL|EUPL|"
    r"AFL|OSL|Artistic|BSL|CC-BY|Unicode|OLDAP|PSF|Python|HPND|RSA-MD)(?:-|$)|"
    r"(?:MIT|ISC|ICU|Zlib|curl|Spencer-94|bzip2-1\.0\.[46])$"
)
COPYRIGHT_NOT_REQUIRED_LICENSES = NON_OSS_LICENSES | {"PD", "CC0-1.0", "Unlicense"}


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
    # Prebuilt Go recipes supply actual source separately from their unpacked
    # binary inputs. Prefer that collection to create-spdx's binary archive.
    clearing = deploy_dir / "sources" / f"{recipe}-{version}"
    if (clearing / "go-clearing-report.json").is_file():
        return clearing
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
        newest = max(candidates, key=lambda path: path.stat().st_mtime)
        if archive_has_files(newest):
            return newest

    # OE's create-spdx class deliberately skips shared GCC source and writes
    # an empty archive for recipes using the shared kernel source tree.  The
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


def git_repository_identifier(location, fallback):
    repository = location.rsplit("@", 1)[0].rstrip("/")
    repository = repository.rsplit("/", 1)[-1]
    if repository.endswith(".git"):
        repository = repository[:-4]
    return repository or fallback


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
        entry = (
            git_repository_identifier(location, package.get("name", "source")),
            match.group(1).lower(),
        )
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


def is_attribution_file(name):
    """Recognize upstream notice documents without treating authors as owners."""
    return bool(re.fullmatch(
        r"(?:authors?|contributors?|copyrights?|copying|licen[cs]es?|notice|"
        r"notices|acknowledg(?:e)?ments|thanks)(?:\.(?:txt|md|rst))?",
        Path(name).name, re.IGNORECASE,
    ))


@contextmanager
def open_source_archive(path, complete=True):
    """Open a source archive as a tar stream.

    With complete=False the caller may stop reading early; the decompressor
    is then stopped and its exit status ignored.
    """
    if not path.name.endswith(".tar.zst"):
        with tarfile.open(path, "r|*") as archive:
            yield archive
        return
    with tempfile.TemporaryFile() as diagnostics:
        process = subprocess.Popen(
            ["zstd", "-dc", "--", str(path)],
            stdout=subprocess.PIPE, stderr=diagnostics,
        )
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                yield archive
            if complete:
                # Drain padding before waiting for the decompressor to finish.
                while process.stdout.read(1024 * 1024):
                    pass
            else:
                process.terminate()
        except BaseException:
            process.terminate()
            raise
        finally:
            process.stdout.close()
            status = process.wait()
        if complete and status:
            diagnostics.seek(0)
            raise ValueError(
                f"cannot read source archive {path}: "
                + diagnostics.read().decode("utf-8", errors="replace")
            )


def archive_has_files(path):
    """Return whether an archive contains at least one regular file."""
    try:
        with open_source_archive(path, complete=False) as archive:
            return any(member.isfile() for member in archive)
    except tarfile.ReadError:
        return False


def read_archive_attributions(path, header_notices=None):
    """Return attribution documents in an archive.

    When header_notices is a set, add copyright statements found in the
    headers of all other regular files to it.
    """
    # Read members directly: never extract untrusted archive paths or links.
    material = []

    def read_members(archive):
        for member in archive:
            if not member.isfile():
                continue
            if is_attribution_file(member.name):
                with archive.extractfile(member) as stream:
                    material.append((
                        f"source/{path.name}/{member.name}",
                        stream.read().decode("utf-8", errors="replace"),
                    ))
            elif header_notices is not None:
                with archive.extractfile(member) as stream:
                    header_notices.update(header_copyright_statements(
                        stream.read(SOURCE_HEADER_BYTES)
                    ))

    with open_source_archive(path) as archive:
        read_members(archive)
    return sorted(material)


SOURCE_ARCHIVE_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar.zst")


def read_source_attributions(source):
    """Return attribution documents and source-header copyright statements."""
    header_notices = set()
    if source.is_file():
        material = read_archive_attributions(source, header_notices)
    else:
        material = []
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(source).as_posix()
            if is_attribution_file(path.name):
                material.append((
                    f"source/{relative}",
                    path.read_text(encoding="utf-8", errors="replace"),
                ))
            elif path.name.endswith(SOURCE_ARCHIVE_SUFFIXES):
                for name, content in read_archive_attributions(path, header_notices):
                    material.append((f"source/{relative}/{name.split('/', 2)[2]}", content))
            else:
                with path.open("rb") as stream:
                    header_notices.update(header_copyright_statements(
                        stream.read(SOURCE_HEADER_BYTES)
                    ))
    if header_notices:
        material.append((
            SOURCE_HEADER_NOTICES,
            "\n".join(sorted(header_notices, key=str.casefold)),
        ))
    return material


def read_spdx_copyrights(deploy_dir, recipe):
    path = find_recipe_spdx(deploy_dir, recipe)
    if not path:
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    notices = []
    for kind, field in (("packages", "copyrightText"), ("files", "copyrightText")):
        for entry in document.get(kind, []):
            notice = entry.get(field, "").strip()
            if notice and notice not in {"NONE", "NOASSERTION"}:
                name = entry.get("fileName", entry.get("name", recipe))
                notices.append(f"{name}\n{notice}")
    return [("SPDX copyright statements", "\n\n".join(sorted(set(notices))))] if notices else []


def license_tokens(expression):
    """Return license IDs, without operators and WITH exceptions."""
    # Exceptions modify license conditions, but do not remove notice checks.
    expression = re.sub(r"\bWITH\s+[A-Za-z0-9._+-]+", "", expression)
    return set(LICENSE_TOKEN.findall(expression)) - {"AND", "OR"}


def is_closed_license(expression):
    tokens = license_tokens(expression)
    return bool(tokens) and tokens <= NON_OSS_LICENSES


def requires_corresponding_source(expression):
    return any(
        SOURCE_REQUIRED_LICENSE.match(token) for token in license_tokens(expression)
    )


def copyright_license_policy(expression):
    """Return required, unknown IDs. Unknown licenses need policy review."""
    required = False
    unknown = []
    for token in sorted(license_tokens(expression)):
        if COPYRIGHT_REQUIRED_LICENSE.match(token):
            required = True
        elif token not in COPYRIGHT_NOT_REQUIRED_LICENSES:
            unknown.append(token)
    return required, unknown


# "Copyright [(C)] YEAR holder", "SPDX-FileCopyrightText: YEAR holder" or a
# bare "(C)"/"©" YEAR holder, as in zlib's LICENSE file.
COPYRIGHT_LINE = re.compile(
    r"(?:\bSPDX-FileCopyrightText:|\bcopyright\b|(?<!\w)(?=\(c\)|©))\s*"
    r"(?:(?:\(c\)|©)\s*)?"
    r"((?:19|20)\d{2}[\d\s,–-]*)\s+(.+)",
    re.IGNORECASE,
)
# Template placeholders. Real holders may contain e-mail addresses in <...>.
COPYRIGHT_PLACEHOLDER = re.compile(
    r"<[^>]*\b(?:year|(?:full)?name|owner|holders?|author|copyright|organi[sz]ation)\b[^>]*>|"
    r"\[[^\]]*\b(?:year|(?:full)?name|owner|holders?|author|copyright|organi[sz]ation)\b[^\]]*\]|"
    r"\b(?:yyyy|name of|copyright holder|your name)\b",
    re.IGNORECASE,
)


GENERIC_GNU_LICENSE = re.compile(
    r"GNU\s+(?:LESSER\s+|AFFERO\s+|LIBRARY\s+)?GENERAL\s+PUBLIC\s+LICENSE",
    re.IGNORECASE,
)


def copyright_statements(text):
    """Return component copyright statements found in text.

    Template placeholders and the FSF copyright on GNU license texts are
    skipped: they do not name a holder of the component.
    """
    generic_gnu_license = (
        bool(GENERIC_GNU_LICENSE.search(text))
        and "Everyone is permitted to copy" in text
    )
    statements = []
    for line in text.splitlines():
        match = COPYRIGHT_LINE.search(line)
        if not match:
            continue
        holder = match.group(2).strip().strip("*/# ")
        if not holder or COPYRIGHT_PLACEHOLDER.search(holder):
            continue
        if generic_gnu_license and "Free Software Foundation" in holder:
            continue  # Copyright in the license itself is not component evidence.
        if not any(character.isalpha() for character in holder):
            continue
        statement = " ".join(line[match.start():].split()).rstrip("*/# ")
        if statement not in statements:
            statements.append(statement)
    return statements


def header_copyright_statements(header):
    """Return copyright statements from the leading bytes of a source file."""
    if b"\0" in header:
        return []  # Binary file.
    return copyright_statements(header.decode("utf-8", errors="replace"))


def has_copyright_information(material):
    """Check collected evidence, excluding generic license/template notices.

    This is a presence check, not an audit of every copyright holder.
    Author lists alone are not copyright evidence.
    """
    for name, text in material:
        if name == "SPDX copyright statements":
            # SPDX copyrightText is explicitly asserted metadata, rather than
            # a license template. read_spdx_copyrights removes unknown sentinels.
            if text.strip():
                return True
        if copyright_statements(text):
            return True
    return False


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


def summary_source_revision_text(revisions):
    if not revisions:
        return "—"
    if len(revisions) == 1:
        return revisions[0][1]
    return f"{len(revisions)} revisions (see component details)"


def source_revision_html(revisions):
    if not revisions:
        return html.escape(source_revision_text(revisions))
    items = "".join(
        f"<li><strong>{html.escape(repository)}</strong>: "
        f"<code>{html.escape(revision)}</code></li>"
        for repository, revision in revisions
    )
    return f"<ul>{items}</ul>"


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
            f"<td>{html.escape(summary_source_revision_text(revisions_by_recipe[recipe]))}</td>"
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
<th>Source revision(s)</th><th>Applicable license (SPDX expression)</th></tr></thead>
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
<dt>Source repositories and revisions</dt>
<dd>{source_revision_html(revisions_by_recipe[recipe])}</dd>
<dt>Declared license(s)</dt><dd>{html.escape(', '.join(licenses))}</dd>
<dt>Full license text</dt><dd>Included verbatim in the legal material below.</dd>
<dt>Copyright notices</dt><dd>Collected notices, SPDX statements and copyright
lines found in source file headers appear below.</dd>
<dt>Acknowledgements and additional mandatory information</dt>
<dd>Collected upstream attribution documents appear below. Author and contributor
lists are preserved as attribution; they do not establish copyright ownership.</dd>
<dt>Corresponding source in this delivery</dt><dd>{html.escape(source_text)}</dd>
</dl>
<h2>Installed packages produced by this component</h2>
<table><thead><tr><th>Installed package</th><th>Version</th>
<th>Applicable license (SPDX expression)</th></tr></thead>
<tbody>{package_rows}</tbody></table>
<h2>License texts, copyright notices and acknowledgements</h2>
<p>Source attribution documents retain their archive paths and original text.
Source file copyright notices are the distinct copyright lines found at the top
of each source file. This collection does not establish that every source-level
notice was identified.</p>
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
        legal_by_recipe[recipe].extend(read_source_attributions(artifact))

    for recipe in recipe_versions:
        legal_by_recipe[recipe].extend(read_spdx_copyrights(deploy_dir, recipe))
        licenses = sorted({
            item["LICENSE"] for item in components if item["RECIPE NAME"] == recipe
        })
        required = False
        unknown = set()
        for expression in licenses:
            needs_notice, unreviewed = copyright_license_policy(expression)
            required |= needs_notice
            unknown.update(unreviewed)
        if unknown:
            errors.append(
                f"{recipe}: copyright notice policy is unknown for "
                + ", ".join(sorted(unknown)) + "; review and extend the license policy"
            )
        if required and not has_copyright_information(legal_by_recipe[recipe]):
            errors.append(
                f"{recipe}: declared license ({', '.join(licenses)}) requires "
                "copyright notices, but no component copyright information "
                "was collected; provide an upstream notice in deployed legal "
                "material or populate SPDX copyrightText (AUTHORS alone and "
                "generic license texts do not suffice)"
            )

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
    except (OSError, ValueError, tarfile.TarError, json.JSONDecodeError) as error:
        die(str(error))
