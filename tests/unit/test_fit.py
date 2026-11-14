"""In-place FIT signature manipulation used by the CoreOS rejection test."""
import struct

import pytest

from yocto_tests.fit import fit_nodes, tamper_fit

VALUE = bytes(range(256))


def fdt(tree):
    """Build a flattened device tree from {name: {prop: bytes} | subtree}."""
    strings, struct_block = bytearray(), bytearray()

    def pad():
        struct_block.extend(b"\0" * (-len(struct_block) % 4))

    def node(name, content):
        struct_block.extend(struct.pack(">I", 1) + name.encode() + b"\0")
        pad()
        for key, value in content.items():
            if isinstance(value, bytes):
                if key.encode() + b"\0" not in strings:
                    strings.extend(key.encode() + b"\0")
                struct_block.extend(struct.pack(">3I", 3, len(value), strings.index(key.encode() + b"\0")))
                struct_block.extend(value)
                pad()
        for key, value in content.items():
            if isinstance(value, dict):
                node(key, value)
        struct_block.extend(struct.pack(">I", 2))

    node("", tree)
    struct_block.extend(struct.pack(">I", 9))
    off_struct = 40 + 16
    off_strings = off_struct + len(struct_block)
    header = struct.pack(">10I", 0xD00DFEED, off_strings + len(strings), off_struct, off_strings,
                         40, 17, 16, 0, len(strings), len(struct_block))
    return header + bytes(16) + bytes(struct_block) + bytes(strings)


TREE = {
    "images": {"kernel-1": {"data": b"kernel", "hash-1": {"value": b"h" * 32}}},
    "configurations": {
        "default": b"conf-1\0",
        "conf-1": {"kernel": b"kernel-1\0", "signature-1": {"key-name-hint": b"dev\0", "value": VALUE}},
        "conf-2": {"fdt": b"fdt-1\0", "signature-1": {"key-name-hint": b"dev\0", "value": VALUE}},
    },
}


def signatures(path):
    blob = path.read_bytes()
    return {p: blob[slice(props["value"][0], sum(props["value"]))]
            for p, _, props in fit_nodes(blob) if "/configurations/" in p and "ignature" in p}


@pytest.fixture
def fit(tmp_path):
    path = tmp_path / "fitImage"
    path.write_bytes(fdt(TREE))
    return path


def test_unsigned_renames_only_configuration_signatures(fit):
    size = fit.stat().st_size
    assert tamper_fit(fit, "unsigned") == 2
    assert fit.stat().st_size == size
    found = signatures(fit)
    assert set(found) == {"/configurations/conf-1/xignature-1", "/configurations/conf-2/xignature-1"}
    assert all(value == VALUE for value in found.values())
    assert any(p.endswith("/images/kernel-1/hash-1") for p, _, _ in fit_nodes(fit.read_bytes()))


def test_wrong_signature_replaces_values(fit):
    size = fit.stat().st_size
    assert tamper_fit(fit, "wrong-signature") == 2
    assert fit.stat().st_size == size
    found = signatures(fit)
    assert set(found) == {"/configurations/conf-1/signature-1", "/configurations/conf-2/signature-1"}
    assert all(len(value) == len(VALUE) and value != VALUE for value in found.values())


def test_unsigned_fit_is_rejected(tmp_path):
    path = tmp_path / "fitImage"
    path.write_bytes(fdt({"configurations": {"conf-1": {"kernel": b"kernel-1\0"}}}))
    with pytest.raises(AssertionError, match="no configuration signatures"):
        tamper_fit(path, "unsigned")
