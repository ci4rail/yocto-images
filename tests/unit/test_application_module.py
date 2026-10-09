"""Validate module configuration without an engine or writable rootfs."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / 'cpu01-standard-image/src/meta-ci4rail-bsp/recipes-mender/mender-docker-compose/files'


@pytest.fixture
def helper(tmp_path):
    docker = tmp_path / 'docker'
    docker.write_text('''#!/bin/sh
if test "$1" = compose; then
    printf '%s\\n' "$TEST_COMPOSE_VERSION"
fi
''')
    docker.chmod(0o755)

    def run(version='2.26.0', **settings):
        env = dict(os.environ, DOCKER_COMMAND=str(docker),
                   DOCKER_COMPOSE_COMMAND='', TEST_COMPOSE_VERSION=version,
                   TMPDIR=str(tmp_path), MENDER_APP_COMPOSE_CONFIG_FILE=str(tmp_path/'absent'))
        env.update(settings)
        return subprocess.run(['sh', str(FILES/'docker-compose'), 'REQS'],
                              env=env, text=True, capture_output=True)
    return run


@pytest.mark.parametrize('version,success', [
    ('2.26.0', True), ('v2.26.0', True), ('1.25.5', False), ('podman-compose 1.0.6', False),
])
def test_requires_compose_v2(helper, version, success):
    result = helper(version)
    assert (result.returncode == 0) == success, result.stderr


@pytest.mark.parametrize('settings', [
    {'APP_HEALTHCHECK_ENABLED': 'maybe'},
    {'APP_HEALTHCHECK_ENABLED': 'yes', 'APP_HEALTHCHECK_TIMEOUT': '0'},
    {'APP_HEALTHCHECK_ENABLED': 'yes', 'APP_HEALTHCHECK_INTERVAL': '-1'},
    {'APP_HEALTHCHECK_ENABLED': 'yes', 'APP_HEALTHCHECK_TIMEOUT': 'abc'},
])
def test_invalid_health_settings_fail_before_install(helper, settings):
    assert helper(**settings).returncode != 0
