"""Build signed os-customization Mender artifacts for hardware tests."""
import json
from pathlib import Path
import tarfile

from .core_api import artifact_command
from .ota import sign_artifact

ENDPOINT = "/software/core-os-customization"
NAME = "core-os-customization"


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
        tar.add(manifest, arcname="manifest.json")
        # Symlinks stay symlinks: os-customization validates their targets.
        tar.add(etc if etc else payload / "etc", arcname="etc")
    artifact = directory / "customization.mender"
    artifact_command([tool, "write", "module-image", "-T", "os-customization",
                      "-n", version, "-t", device_type, "-f", archive,
                      "-o", artifact])
    sign_artifact(tool, artifact, cfg, "os-customization")
    return artifact
