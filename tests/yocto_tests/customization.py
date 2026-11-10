"""Build signed os-customization Mender artifacts for hardware tests."""
import json
from pathlib import Path
import tarfile

from .core_api import artifact_command
from .ota import sign_artifact

ENDPOINT = "/software/core-os-customization"
NAME = "core-os-customization"


# git keeps no file modes, and the checkout's umask may leave files group-writable
# (dropbear then ignores authorized_keys). Modes for files that need stricter ones:
# NetworkManager ignores connection files readable by others.
PAYLOAD_MODES = {"etc/ci4rail/user-passwords/": 0o600, "etc/sudoers.d/": 0o440,
                 "etc/NetworkManager/system-connections/": 0o600}


def payload_mode(member):
    """Root-owned members without group/other write; secrets get their own modes."""
    member.uid = member.gid = 0
    member.uname = member.gname = "root"
    if member.isfile():
        member.mode &= ~0o022
        for prefix, mode in PAYLOAD_MODES.items():
            if member.name.startswith(prefix):
                member.mode = mode
    elif member.isdir():
        member.mode &= ~0o022
    return member


def build_customization(tool, version, device_type, directory, cfg, etc=None):
    """Write <directory>/customization.mender and return its path.

    `etc` is a local directory that becomes the payload's etc/ tree; without it
    the payload only contains an empty etc/.
    """
    directory = Path(directory)
    payload = directory / "payload"
    (payload / "etc").mkdir(parents=True)
    manifest = payload / "manifest.json"
    manifest.write_text(json.dumps({
        "format_version": 1, "version": version,
        "health_checks": [{"type": "command", "command": ["true"]}],
    }))
    archive = directory / "customization.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(manifest, arcname="manifest.json", filter=payload_mode)
        # Symlinks stay symlinks: os-customization validates their targets.
        tar.add(etc if etc else payload / "etc", arcname="etc", filter=payload_mode)
    artifact = directory / "customization.mender"
    artifact_command([tool, "write", "module-image", "-T", "os-customization",
                      "-n", version, "-t", device_type, "-f", archive,
                      "-o", artifact])
    sign_artifact(tool, artifact, cfg, "os-customization")
    return artifact
