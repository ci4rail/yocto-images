"""Shared OTA signing and factory provisioning for hardware tests."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import uuid

import paramiko

from .core_api import STAGING_KEYS
from .station import quote


def signing_material(cfg):
    value = cfg.get("ota_signing_material_dir")
    if value == "staging":
        # The repository's staging keys, or the copy bundled in the pytest package.
        return STAGING_KEYS
    return Path(value).expanduser().resolve() if value else None


def signing_key(cfg, kind):
    keys = signing_material(cfg)
    if keys is None:
        return None
    name = "ci4rail-artifact" if kind == "core-os" else "customer-artifact"
    key = keys / (name + ".key.pem")
    assert key.is_file(), f"Missing OTA signing key: {key}"
    return str(key)


def sign_artifact(tool, artifact, cfg, kind):
    key = signing_key(cfg, kind)
    if key:
        # Older artifact tools require traditional PEM instead of PKCS#8.
        with tempfile.TemporaryDirectory(prefix="yocto-signing-") as directory:
            converted = Path(directory) / "key.pem"
            converted.touch(mode=0o600)
            result = subprocess.run(["openssl", "pkey", "-traditional", "-in", key,
                                     "-out", str(converted)], capture_output=True,
                                    text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            result = subprocess.run([str(tool), "sign", "--force", "-k", str(converted), str(artifact)],
                                    capture_output=True, text=True, timeout=300)
            assert result.returncode == 0, result.stdout + result.stderr


BAD_SIGNATURES = ("unsigned", "unknown-key")


def break_signature(tool, artifact, mode):
    """Make the OTA signature of artifact invalid for the device, in place.

    "unsigned" drops manifest.sig; "unknown-key" re-signs with a throwaway key
    that is neither the ci4rail nor the provisioned customer key.
    """
    assert mode in BAD_SIGNATURES, mode
    artifact = Path(artifact)
    with tempfile.TemporaryDirectory(prefix="yocto-badsig-", dir=artifact.parent) as directory:
        if mode == "unsigned":
            stripped = Path(directory) / "artifact.mender"
            with tarfile.open(artifact) as source, tarfile.open(stripped, "w", format=tarfile.GNU_FORMAT) as target:
                for member in source:
                    if member.name != "manifest.sig":
                        target.addfile(member, source.extractfile(member) if member.isfile() else None)
            stripped.replace(artifact)
        else:
            generated, key = Path(directory) / "unknown.pem", Path(directory) / "unknown-traditional.pem"
            for command in (["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
                             "-out", str(generated)],
                            # Older artifact tools require traditional PEM instead of PKCS#8.
                            ["openssl", "pkey", "-traditional", "-in", str(generated), "-out", str(key)],
                            [str(tool), "sign", "--force", "-k", str(key), str(artifact)]):
                result = subprocess.run(command, capture_output=True, text=True, timeout=300)
                assert result.returncode == 0, result.stdout + result.stderr


def assert_signature_invalid(tool, artifact, cfg):
    """Neither device verification key (ci4rail, customer) accepts the artifact."""
    keys = signing_material(cfg)
    if keys is None:
        return
    for name in ("ci4rail-artifact-pub.pem", "customer-artifact-pub.pem"):
        result = subprocess.run([str(tool), "validate", "-k", str(keys / name), str(artifact)],
                                capture_output=True, text=True, timeout=300)
        assert result.returncode != 0, f"{artifact} still validates with {name}"


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def provision_factory(s):
    keys = signing_material(s.cfg)
    if keys is None:
        return
    names = ("customer-artifact-pub.pem", "customer-artifact-pub.pem.sig")
    for name in names:
        assert (keys / name).is_file(), f"Missing OTA provisioning file: {keys / name}"
    for kind in ("core-os", "application"):
        signing_key(s.cfg, kind)
    dut = s.dut
    # Later pytest invocations on the same flash (--skip-flash) must not reinstall the
    # factory layer: that costs a reboot and may drop installed customizations.
    if all(sha256_file(keys / name) in dut.run(f"sha256sum /etc/ota/{name} 2>/dev/null", check=False)
           for name in names):
        return
    remote = "/data/yocto-ota-" + uuid.uuid4().hex
    # install-factory replaces the layer: copy it first to retain existing files.
    dut.run(f"set -e\nmkdir -p {remote}/etc\n"
            f"if test -d /data/os-customization/factory; then "
            f"cp -a /data/os-customization/factory/. {remote}/; fi\n"
            f"test ! -L {remote}/etc/ota\n"
            f"mkdir -p {remote}/etc/ota\n"
            f"rm -f {remote}/etc/ota/customer-artifact-pub.pem "
            f"{remote}/etc/ota/customer-artifact-pub.pem.sig")
    try:
        for name in names:
            dut.put(keys / name, remote + "/etc/ota/" + name)
        with tempfile.TemporaryDirectory(prefix="yocto-ota-") as local:
            manifest = Path(local) / "manifest.json"
            manifest.write_text(json.dumps({"format_version": 1, "version": "test-ota-keys"}))
            dut.put(manifest, remote + "/ota-manifest.json")
        dut.run(f"set -e\ntest -f {remote}/manifest.json || "
                f"cp {remote}/ota-manifest.json {remote}/manifest.json\n"
                f"os-customization-set install-factory {quote(remote)}")
    finally:
        dut.run(f"rm -rf {quote(remote)}")
    boot = dut.run("cat /proc/sys/kernel/random/boot_id").strip()
    dut.run("nohup sh -c 'sleep 2; systemctl reboot' >/dev/null 2>&1 </dev/null &")
    deadline = time.monotonic() + s.cfg.get("boot_timeout", 300)
    while time.monotonic() < deadline:
        time.sleep(3)
        try:
            current = dut.run("cat /proc/sys/kernel/random/boot_id", timeout=10).strip()
        except (OSError, RuntimeError, TimeoutError, paramiko.SSHException):
            continue
        if current != boot:
            break
    else:
        raise AssertionError("Target did not reboot to activate OTA factory provisioning")
    for name in names:
        dut.run(f"test ! -L /etc/ota && test ! -L /etc/ota/{name} && "
                f"cmp /data/os-customization/factory/etc/ota/{name} /etc/ota/{name}")
