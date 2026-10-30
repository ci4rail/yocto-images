"""Host-only checks of customer profiles: baselines, payloads and versioning."""
from pathlib import Path
import shutil
import tarfile

import pytest
import yaml

from yocto_tests import profiles
from yocto_tests.customization import build_customization

COMMON = yaml.safe_load((Path(__file__).parents[1] / "config/security_baseline.yaml").read_text())["security_baseline"]


@pytest.mark.parametrize("name", profiles.profile_names())
def test_every_profile_loads_and_has_a_payload(name):
    profile = profiles.load_profile(name)
    assert (profiles.etc_dir(profile) / "ci4rail-profile").read_text().strip() == name


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match="Unknown profile"):
        profiles.load_profile("nope")


def test_merge_replaces_lists_and_null_and_merges_dicts():
    base = {"a": [1, 2], "b": {"x": 1, "y": 2}, "c": {"z": 1}}
    merged = profiles.merge(base, {"a": [3], "b": {"y": 5}, "c": None})
    assert merged == {"a": [3], "b": {"x": 1, "y": 5}, "c": None}
    assert base["a"] == [1, 2] and base["b"]["y"] == 2, "base must not be modified"


def test_without_profile_the_common_baseline_applies():
    assert profiles.effective_baseline(COMMON, None) == COMMON


def test_internet_connected_baseline():
    baseline = profiles.effective_baseline(COMMON, profiles.load_profile("internet-connected"))
    assert baseline["allowed_tcp_ports"] == [22]
    assert baseline["allowed_udp_ports"] == []
    assert baseline["egress_monitor"] is None
    assert baseline["abuse_test"]["tcp_iterations"] == COMMON["abuse_test"]["tcp_iterations"]
    assert "avahi-daemon" not in baseline["allowed_root_network_processes"]


def test_standalone_baseline_only_allows_ssh_https_snmp():
    baseline = profiles.effective_baseline(COMMON, profiles.load_profile("standalone"))
    assert baseline["allowed_tcp_ports"] == [22, 443]
    assert baseline["allowed_udp_ports"] == [161]
    assert baseline["egress_monitor"]["allowed_udp_dports"] == [67, 123]
    assert baseline["egress_monitor"]["allowed_tcp_dports"] == []
    assert baseline["abuse_test"]["udp_ports"] == [161]


def test_standalone_snmp_credentials_match_customization():
    profile = profiles.load_profile("standalone")
    conf = (profiles.etc_dir(profile) / "snmp/snmpd.conf").read_text()
    cfg = profile["snmpv3"]
    assert (f'createUser {cfg["user"]} {cfg["auth_protocol"]} "{cfg["auth_passphrase"]}" '
            f'{cfg["priv_protocol"]} "{cfg["priv_passphrase"]}"') in conf
    assert f'rouser {cfg["user"]} priv' in conf
    assert "community" not in "\n".join(l for l in conf.splitlines() if not l.startswith("#"))


def test_standalone_firewall_matches_baseline():
    profile = profiles.load_profile("standalone")
    rules = (profiles.etc_dir(profile) / "nftables.conf").read_text()
    assert "tcp dport { 22, 443 } accept" in rules and "udp dport 161 accept" in rules
    assert rules.count("policy drop") == 2, "input and output default to drop"
    assert "tailscale" not in rules


def test_standalone_never_falls_back_to_public_dns():
    conf = (profiles.etc_dir(profiles.load_profile("standalone")) / "systemd/resolved.conf.d/10-no-fallback-dns.conf").read_text()
    assert "\nFallbackDNS=\n" in conf


def test_standalone_has_no_dns_driven_clients():
    etc = profiles.etc_dir(profiles.load_profile("standalone"))
    chrony = [l for l in (etc / "chrony.conf").read_text().splitlines() if l and not l.startswith("#")]
    assert not [l for l in chrony if l.split()[0] in ("server", "pool", "peer")], "NTP servers must not be names"
    assert "sourcedir /run/chrony-dhcp" in chrony
    for unit in ("mender-updated", "mender-authd", "mender-connect"):
        assert (etc / f"systemd/system/{unit}.service.d/10-disable.conf").is_file()


def test_stand_in_container_only_answers_on_root():
    assert "location = / {" in profiles.NGINX_CONF and "location / { return 404; }" in profiles.NGINX_CONF


def test_internet_connected_firewall_closes_ingress_and_opens_egress():
    profile = profiles.load_profile("internet-connected")
    rules = (profiles.etc_dir(profile) / "nftables.conf").read_text()
    assert "hook input" in rules and "hook output" not in rules
    assert 'iifname "tailscale0" tcp dport 22 accept' in rules
    assert "dport 443" not in rules


def test_payload_version_follows_content(tmp_path, monkeypatch):
    tree = tmp_path / "os-customizations/p/etc"
    tree.mkdir(parents=True)
    (tree / "f").write_text("one")
    monkeypatch.setattr(profiles, "CUSTOMIZATION_DIR", tmp_path / "os-customizations")
    profile = {"name": "p", "customization": "p"}
    first = profiles.payload_version(profile)
    assert profiles.payload_version(profile) == first
    (tree / "f").write_text("two")
    assert profiles.payload_version(profile) != first
    assert first.startswith("profile-p-")


@pytest.mark.parametrize("name", profiles.profile_names())
def test_customization_artifact_contains_payload(tmp_path, name):
    tool = shutil.which("mender-artifact")
    if not tool:
        pytest.skip("mender-artifact not installed")
    profile = profiles.load_profile(name)
    artifact = build_customization(tool, "profile-test", "moducop-cpu01", tmp_path, {},
                                   etc=profiles.etc_dir(profile))
    assert artifact.is_file()
    with tarfile.open(tmp_path / "customization.tar") as tar:
        names = tar.getnames()
    assert "manifest.json" in names and "etc/ci4rail-profile" in names


class FakeAPI:
    def __init__(self, station, current):
        self.current, self.deployed = current, []

    def wait_ready(self):
        pass

    def request(self, endpoint):
        return 200, {"current": self.current}

    def deploy(self, endpoint, artifact, expected):
        self.deployed.append((endpoint, expected))


class FakeDut:
    def __init__(self):
        self.commands = []

    def run(self, command, **kwargs):
        self.commands.append(command)
        return "device_type=moducop-cpu01\n"

    def put(self, source, destination):
        self.commands.append(f"put {destination}")


def test_ensure_profile_is_idempotent(tmp_path, monkeypatch):
    profile = profiles.load_profile("internet-connected")
    expected = {"name": "core-os-customization", "version": profiles.payload_version(profile)}
    api = FakeAPI(None, expected)
    monkeypatch.setattr(profiles, "CoreAPI", lambda station: api)
    station = type("S", (), {"dut": FakeDut(), "cfg": {}})()
    assert profiles.ensure_profile(station, profile, tmp_path) is False
    assert not api.deployed and not station.dut.commands


def test_ensure_profile_deploys_when_other_customization_is_installed(tmp_path, monkeypatch):
    if not shutil.which("mender-artifact"):
        pytest.skip("mender-artifact not installed")
    profile = profiles.load_profile("internet-connected")
    api = FakeAPI(None, {"name": "core-os-customization", "version": "profile-standalone-0"})
    monkeypatch.setattr(profiles, "CoreAPI", lambda station: api)
    station = type("S", (), {"dut": FakeDut(), "cfg": {}})()
    assert profiles.ensure_profile(station, profile, tmp_path) is True
    [(endpoint, expected)] = api.deployed
    assert endpoint == "/software/core-os-customization"
    assert expected["version"] == profiles.payload_version(profile)
