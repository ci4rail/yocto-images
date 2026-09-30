"""Host-only checks for configuration, secret handling and failure cleanup."""
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from yocto_tests.station import Host, load_config, quote
from yocto_tests.setup import prepare
from yocto_tests.security_network_inventory import (
    parse_lsof_network_inventory, get_service_socket_inventory,
    root_network_service_processes_should_match_baseline,
)
from yocto_tests.security_nft_egress import parse_nft_counter_comments

ROOT = Path(__file__).parents[1]


def test_config_environment_and_closed_guard(tmp_path, monkeypatch):
    path = tmp_path / 'station.yaml'
    path.write_text('platform: cpu01plus\ntc: {local: true}\ntarget:\n  host: ${TARGET_IP}\n')
    monkeypatch.delenv('TARGET_IP', raising=False)
    with pytest.raises(ValueError, match='TARGET_IP'):
        load_config(path)
    monkeypatch.setenv('TARGET_IP', '192.0.2.1')
    monkeypatch.setenv('DOCKER_PASSWORD', 'test-secret')
    path.write_text(path.read_text() + 'docker_password: $DOCKER_PASSWORD\n')
    assert load_config(path)['target']['host'] == '192.0.2.1'
    assert load_config(path)['docker_password'] == 'test-secret'
    path.write_text(path.read_text() + 'closed: true\ninstaller_image: ci4rail/tdx-installer-dev:v3\n')
    with pytest.raises(ValueError, match='signed installer'):
        load_config(path)


def test_shell_quoting_and_sensitive_output(tmp_path):
    host = Host({'local': True}, tmp_path, 'local')
    payload = 'a space; $(touch MUST_NOT_EXIST) `echo unsafe`'
    assert host.run('printf %s ' + quote(payload)) == payload
    assert host.run('cat', input='secret-value', secret=True) == 'secret-value'
    with pytest.raises(RuntimeError, match='sensitive command failed') as error:
        host.run('cat; exit 1', input='secret-value', secret=True)
    assert 'secret-value' not in str(error.value)
    assert 'secret-value' not in host.log.read_text()


def test_prepare_releases_lock_after_failed_boot(tmp_path):
    tc = Host({'local': True}, tmp_path, 'tc')
    # Exercise real directory locking/cleanup without Docker or hardware.
    original = tc.run
    def run(command, **kwargs):
        if command.startswith('docker info'):
            return ''
        return original(command, **kwargs)
    tc.run = run
    def failed_boot():
        raise RuntimeError('offline')
    station = SimpleNamespace(tc=tc, cfg={'tc': {'work_dir': str(tmp_path / 'work')}}, wait_boot=failed_boot)
    with pytest.raises(RuntimeError, match='offline'):
        with prepare(station, skip_flash=True):
            pytest.fail('Should not reach test execution')
    assert list((tmp_path / 'work').iterdir()) == []


def test_existing_lock_is_not_removed(tmp_path):
    base = tmp_path / 'work'
    (base / 'station.lock').mkdir(parents=True)
    station = SimpleNamespace(tc=Host({'local': True}, tmp_path, 'tc'), cfg={'tc': {'work_dir': str(base)}})
    with pytest.raises(RuntimeError):
        with prepare(station, skip_flash=True):
            pytest.fail('Should not obtain an occupied station')
    assert (base / 'station.lock').is_dir()


def test_network_inventory_rejects_unapproved_root_service():
    raw = 'p1\ncunknown\nu0\nLroot\nPTCP\nn*:22\nTST=LISTEN\nPTCP\nn127.0.0.1:80\nTST=LISTEN\n'
    services = get_service_socket_inventory(parse_lsof_network_inventory(raw))
    assert len(services) == 1
    with pytest.raises(AssertionError, match='unknown'):
        root_network_service_processes_should_match_baseline(services, ['sshd'])


def test_nft_counters_accumulate():
    raw = 'counter packets 2 bytes 40 comment "unexpected_egress"\n'
    assert parse_nft_counter_comments(raw + raw)['unexpected_egress'] == {'packets': 4, 'bytes': 80}


def test_package_allowlist(tmp_path):
    archive = tmp_path / 'suite.tar.gz'
    subprocess.run(['bash', str(ROOT / 'scripts/package.sh'), str(archive)], check=True)
    with tarfile.open(archive) as package:
        names = package.getnames()
    assert 'run.sh' in names and 'yocto_tests/test_security.py' in names
    assert 'config/station.example.yaml' in names
    assert not any('station.yaml' in p or '.venv' in p or 'results/' in p or '__pycache__' in p for p in names)


@pytest.mark.parametrize('platform,machine', [('cpu01', 'verdin-imx8mm'), ('cpu01plus', 'verdin-imx8mp')])
def test_installer_machine_and_timeout_cleanup(tmp_path, monkeypatch, platform, machine):
    from yocto_tests import setup
    image = tmp_path / 'image.tar'
    image.touch()
    commands = []
    class TC:
        def put(self, source, destination):
            assert Path(source).is_file()
        def run(self, command, **kwargs):
            commands.append(command)
            if command.startswith('df -B1'):
                return str(10 * 1024**3)
            if command.startswith('test -c'):
                return 'ready'
            if command.startswith('docker run'):
                raise TimeoutError('installer timeout')
            return ''
    def convert(args, **kwargs):
        Path(args[-1]).touch()
    monkeypatch.setattr(setup.subprocess, 'run', convert)
    monkeypatch.setattr(setup, 'power_on', lambda cfg: None)
    cfg = dict(image_file=str(image), platform=platform, tc={'host': 'tc'},
               relay={'host': 'tc', 'port': 4223, 'uid': 'TeL', 'power_channel': 0, 'recovery_channel': 1},
               installer_image='ci4rail/tdx-installer-dev:v3', serial_port='/dev/ttyMODUCOP')
    station = SimpleNamespace(cfg=cfg, tc=TC(), results=tmp_path)
    with pytest.raises(TimeoutError):
        setup.flash(station, '/tmp/work')
    run = next(c for c in commands if c.startswith('docker run'))
    assert 'docker run -t ' in run
    assert f'-m {machine}' in run
    assert '-e BRICKD_UID=TeL' in run
    assert commands[-1].startswith('docker rm -f yocto-installer-')


def test_power_cycle_releases_recovery_and_restores_power(monkeypatch):
    from yocto_tests import setup
    calls = []
    class Connection:
        def connect(self, host, port):
            calls.append(('connect', host, port))
        def disconnect(self):
            calls.append(('disconnect',))
    class Relay:
        def __init__(self, uid, connection):
            assert uid == 'Nqy'
        def set_selected_value(self, channel, value):
            calls.append((channel, value))
    monkeypatch.setattr(setup, 'IPConnection', Connection)
    monkeypatch.setattr(setup, 'BrickletIndustrialDualRelay', Relay)
    monkeypatch.setattr(setup.time, 'sleep', lambda seconds: calls.append(('sleep', seconds)))
    setup.power_cycle({'relay': {'host': 'tc', 'port': 4223, 'uid': 'Nqy',
                                'recovery_channel': 1, 'power_channel': 0}})
    assert calls == [('connect', 'tc', 4223), (1, False), (0, False),
                     ('sleep', 2), (0, True), ('disconnect',)]


@pytest.mark.parametrize('accept_changed', [True, False])
def test_reflashed_target_host_key_policy(tmp_path, monkeypatch, accept_changed):
    from yocto_tests import station
    calls = []
    class Client:
        def load_system_host_keys(self):
            calls.append('load-known-hosts')
        def set_missing_host_key_policy(self, policy):
            calls.append('accept-new')
        def connect(self, *args, **kwargs):
            pass
    monkeypatch.setattr(station.paramiko, 'SSHClient', Client)
    Host({'host': '192.0.2.1', 'allow_unknown_host_key': accept_changed}, tmp_path, 'dut').connect()
    assert calls == (['accept-new'] if accept_changed else ['load-known-hosts'])
