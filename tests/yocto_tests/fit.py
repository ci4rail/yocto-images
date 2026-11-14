"""Repack CoreOS artifacts under a new name, optionally with a manipulated boot FIT.

The FIT is edited in place (no size change), so no U-Boot tools are needed:
- "unsigned": rename every configuration signature node so U-Boot finds none.
- "wrong-signature": replace every configuration signature value with random
  bytes, as if the FIT had been signed with an unknown key.
"""
import json
import os
from pathlib import Path
import struct
import tarfile

from .core_api import artifact_command
from .ota import sign_artifact

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9
TAMPER_MODES = ("unsigned", "wrong-signature")


def _align(offset):
    return (offset + 3) & ~3


def fit_nodes(blob):
    """Yield (path, name_offset, {property: (data_offset, length)}) for each node."""
    magic, _, off_struct, off_strings = struct.unpack_from(">4I", blob, 0)
    assert magic == FDT_MAGIC, "Not a FIT/FDT blob"

    def string(offset):
        return blob[offset:blob.index(b"\0", offset)].decode()

    offset, stack = off_struct, []
    while True:
        (token,) = struct.unpack_from(">I", blob, offset)
        offset += 4
        if token == FDT_BEGIN_NODE:
            name = string(offset)
            stack.append((name, offset, {}))
            offset = _align(offset + len(name) + 1)
        elif token == FDT_END_NODE:
            name, name_offset, props = stack.pop()
            yield "/".join(n for n, _, _ in stack) + "/" + name, name_offset, props
        elif token == FDT_PROP:
            length, name_offset = struct.unpack_from(">2I", blob, offset)
            stack[-1][2][string(off_strings + name_offset)] = (offset + 8, length)
            offset = _align(offset + 8 + length)
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            return
        else:
            raise AssertionError(f"Invalid FDT token {token} at {offset - 4}")


def tamper_fit(path, mode):
    """Manipulate all configuration signatures of the FIT at path; return their count."""
    assert mode in TAMPER_MODES, mode
    blob = bytearray(Path(path).read_bytes())
    signatures = [(p, name_offset, props) for p, name_offset, props in fit_nodes(blob)
                  if p.startswith("/configurations/") and p.rsplit("/", 1)[1].startswith("signature")]
    assert signatures, "FIT has no configuration signatures; is it a secure-boot FIT?"
    for _, name_offset, props in signatures:
        if mode == "unsigned":
            # U-Boot only checks subnodes named signature*; same length keeps the layout.
            blob[name_offset:name_offset + 9] = b"xignature"
        else:
            data, length = props["value"]
            blob[data:data + length] = os.urandom(length)
    Path(path).write_bytes(blob)
    return len(signatures)


def _artifact_headers(artifact):
    with tarfile.open(artifact) as outer:
        header = next(m for m in outer if m.name in ("header.tar.gz", "header.tar"))
        with tarfile.open(fileobj=outer.extractfile(header), mode="r|*") as inner:
            headers = {m.name: json.load(inner.extractfile(m)) for m in inner
                       if m.name in ("header-info", "headers/0000/type-info")}
    return headers["header-info"], headers["headers/0000/type-info"]


def _extract_payloads(artifact, directory):
    files = []
    with tarfile.open(artifact) as outer:
        data = next(m for m in outer if m.name.startswith("data/0000."))
        with tarfile.open(fileobj=outer.extractfile(data), mode="r|*") as inner:
            for member in inner:
                name = Path(member.name).name
                assert member.isfile() and name == member.name, f"Unexpected payload {member.name}"
                with inner.extractfile(member) as src, (directory / name).open("wb") as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
                files.append(directory / name)
    return files


def tampered_core_artifact(tool, source, mode, directory, cfg):
    """Repack source with a manipulated FIT, a new name and a valid OTA signature."""
    return repack_core_artifact(tool, source, directory, cfg, mode, fit_mode=mode)


def repack_core_artifact(tool, source, directory, cfg, suffix, fit_mode=None):
    """Repack source under the name <name>-<suffix>, signed with the valid staging key.

    fit_mode optionally manipulates the boot FIT (see TAMPER_MODES).
    """
    info, type_info = _artifact_headers(source)
    assert type_info["type"] == "bootfit-rootfs", "Use a paired staging FIT/rootfs artifact"
    payloads = directory / "payload"
    payloads.mkdir()
    files = _extract_payloads(source, payloads)
    fits = [f for f in files if not f.name.endswith(".ext4.verity")]
    assert len(fits) == 1 and len(files) == 2, [f.name for f in files]
    if fit_mode:
        tamper_fit(fits[0], fit_mode)
    # A distinct name keeps the failed install apart from the later good CoreOS update.
    name = f"{info['artifact_provides']['artifact_name']}-{suffix}"
    command = [tool, "write", "module-image", "-T", type_info["type"], "-n", name,
               "-o", directory / "core.mender"]
    for device in info["artifact_depends"]["device_type"]:
        command += ["-t", device]
    for key, value in (type_info.get("artifact_provides") or {}).items():
        command += ["--provides", f"{key}:{name if key.endswith('.version') else value}"]
    for pattern in type_info.get("clears_artifact_provides") or []:
        command += ["--clears-provides", pattern]
    for f in files:
        command += ["-f", f]
    artifact_command(command)
    for f in files:
        f.unlink()
    sign_artifact(tool, directory / "core.mender", cfg, "core-os")
    return directory / "core.mender"
