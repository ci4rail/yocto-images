"""Customer security profiles: os-customization payload plus test expectations.

A profile pairs an os-customization payload (os-customizations/<name>/etc) with
the baseline the target is expected to meet once it is installed. Payloads are
built into signed Mender artifacts during the test run and installed through
the core API, the same path a customer's update takes.
"""
import copy
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

import yaml

from .core_api import CoreAPI
from .customization import ENDPOINT, NAME, build_customization
from .station import quote

PROFILE_DIR = Path(__file__).resolve().parents[1] / "config/profiles"
_repo = Path(__file__).resolve().parents[2] / "os-customizations"
# The pytest package ships the payloads next to the tests.
CUSTOMIZATION_DIR = _repo if _repo.is_dir() else Path(__file__).resolve().parents[1] / "os-customizations"


def profile_names():
    return sorted(p.stem for p in PROFILE_DIR.glob("*.yaml"))


def load_profile(name):
    path = PROFILE_DIR / f"{name}.yaml"
    if not path.is_file():
        raise ValueError(f"Unknown profile {name!r}; available: {', '.join(profile_names())}")
    profile = yaml.safe_load(path.read_text())
    assert profile["name"] == name, f"{path}: name must match the file name"
    assert (CUSTOMIZATION_DIR / profile["customization"] / "etc").is_dir(), \
        f"Missing os-customization payload for profile {name}"
    return profile


def merge(base, override):
    """Merge override into base: dicts merge, anything else (lists, null) replaces."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def effective_baseline(common, profile):
    """The common security baseline with the profile's overrides applied."""
    return merge(common, profile.get("security_baseline") if profile else None)


def etc_dir(profile):
    return CUSTOMIZATION_DIR / profile["customization"] / "etc"


def payload_version(profile):
    """Version that changes with the payload, so edits are redeployed and reruns are not."""
    digest = hashlib.sha256()
    root = etc_dir(profile)
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        if path.is_symlink():
            digest.update(b"L" + str(path.readlink()).encode())
        elif path.is_file():
            digest.update(b"F" + path.read_bytes())
        digest.update(b"\0")
    return f"profile-{profile['name']}-{digest.hexdigest()[:12]}"


def artifact_tool(cfg):
    tool = shutil.which(cfg.get("mender_artifact_binary", "mender-artifact"))
    assert tool, "Install mender-artifact on the pytest runner or set mender_artifact_binary"
    return tool


def device_type(dut):
    values = dut.run("cat /var/lib/mender/device_type").splitlines()
    return next(line.split("=", 1)[1] for line in values if line.startswith("device_type="))


def https_ok(dut, https):
    """The customer container answers on the loopback interface, and only on /."""
    url = f"https://127.0.0.1:{int(https['port'])}"
    out = dut.run(f"curl -ksS --max-time 10 {url}/", check=False)
    if https["marker"] not in out:
        return False
    other = dut.run(f"curl -ksS -o /dev/null --max-time 10 --write-out '%{{http_code}}' {url}/no-such-page", check=False)
    assert other.strip() == "404", ("Stale test container answers on HTTPS (HTTP %s for an unknown URL): "
                                    "remove it with docker rm -f and its app under /data/mender-app" % other.strip())
    return True


def ensure_profile(station, profile, tmp_path):
    """Install the profile's customization unless it is already installed.

    Returns True when an update (and thus a target reboot) happened.
    """
    version = payload_version(profile)
    expected = {"name": NAME, "version": version}
    api = CoreAPI(station)
    api.wait_ready()
    status, response = api.request(ENDPOINT)
    if status == 200 and isinstance(response, dict) and response.get("current") == expected:
        return False
    if profile.get("customer_container"):
        ensure_https_container(station, profile, tmp_path)
    dut = station.dut
    tool = artifact_tool(station.cfg)
    artifact = build_customization(tool, version, device_type(dut), tmp_path,
                                   station.cfg, etc=etc_dir(profile))
    remote = "/data/profile-" + uuid.uuid4().hex
    dut.run(f"mkdir -p {remote}")
    try:
        dut.put(artifact, remote + "/customization.mender")
        api.deploy(ENDPOINT, remote + "/customization.mender", expected)
    finally:
        dut.run(f"rm -rf {quote(remote)}", check=False)
    return True


NGINX_CONF = """server {
    listen 443 ssl;
    ssl_certificate /etc/nginx/server.crt;
    ssl_certificate_key /etc/nginx/server.key;
    location = / { return 200 "%s\\n"; }
    location / { return 404; }
}
"""


def ensure_https_container(station, profile, tmp_path):
    """Deploy a stand-in customer container serving HTTPS, as an application update.

    Runs before the restrictive customization is installed: it needs the
    registry. The container uses host networking and survives reboots.
    """
    # Imported here: these live in test modules.
    from .test_application_module import write
    from .test_core_api_server import APIApplication
    dut, https = station.dut, profile["https"]
    if https_ok(dut, https):
        return
    base = station.cfg.get("https_test_image", "nginx:alpine")
    namespace = "yocto-https-" + uuid.uuid4().hex[:12]
    root = f"/data/{namespace}"
    certs = Path(tempfile.mkdtemp(prefix="yocto-https-", dir=tmp_path))
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-subj", "/CN=moducop-profile-test", "-keyout", str(certs / "server.key"),
                    "-out", str(certs / "server.crt")], check=True, capture_output=True, timeout=60)
    dut.run(f"mkdir -p {root}/build")
    try:
        dut.run(f"docker image inspect {quote(base)} >/dev/null 2>&1 || docker pull {quote(base)}", timeout=300)
        write(dut, f"{root}/build/server.key", (certs / "server.key").read_text())
        write(dut, f"{root}/build/server.crt", (certs / "server.crt").read_text())
        write(dut, f"{root}/build/default.conf", NGINX_CONF % https["marker"])
        write(dut, f"{root}/build/Dockerfile",
              f"FROM {base}\nCOPY server.key server.crt /etc/nginx/\n"
              "COPY default.conf /etc/nginx/conf.d/default.conf\n")
        tag = f"{namespace}:new"
        dut.run(f"docker build --network=none -t {tag} {root}/build", timeout=300)
        image = dut.run(f"docker image inspect --format '{{{{.Id}}}}' {tag}").strip()
        app = APIApplication(station, root, {"old": image, "new": image},
                             artifact_tool(station.cfg), Path(tempfile.mkdtemp(dir=tmp_path)))
        artifact = app.artifact("new", command=["nginx", "-g", "daemon off;"],
                                service_extra={"network_mode": "host", "restart": "unless-stopped"})
        api = CoreAPI(station)
        api.deploy(f"/software/application/{app.name}", artifact, {"name": app.name, "version": "new"})
        assert https_ok(dut, https), "Customer container does not answer on HTTPS"
    finally:
        dut.run(f"docker image rm {namespace}:new", check=False)
        dut.run(f"rm -rf {root}", check=False)
