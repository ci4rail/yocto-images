"""Exercise slot switching and root checks without writing device storage."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'cpu01-standard-image/src/meta-ci4rail-bsp/recipes-mender/mender-bootfit-rootfs/files/bootfit-rootfs'


@pytest.fixture
def module(tmp_path):
    source = MODULE.read_text()
    functions, states = source.split('case "${STATE}" in', 1)
    # Replace hardware configuration and root discovery at their boundaries.
    script = tmp_path / 'module'
    script.write_text(functions + '''
load_config() {
    BOOT_SLOT_ENV=mender_boot_part
    BOOT_SLOT_HEX_ENV=mender_boot_part_hex
    SLOT_A_VALUE=2
    SLOT_B_VALUE=3
    ROOTFS_PART_A=/dev/mmcblk0p2
    ROOTFS_PART_B=/dev/mmcblk0p3
    BOOTFIT_TARGET_FILE_A=/fitImage-A
    BOOTFIT_TARGET_FILE_B=/fitImage-B
    UPGRADE_ENV=upgrade_available
    BOOTCOUNT_ENV=bootcount
    CHECK_CMDLINE_ROOT=1
    REQUIRE_DM_VERITY_ROOT=0
}
resolve_kernel_root() { printf '%s\\n' "$ACTUAL_ROOT"; }
readlink() { printf '%s\\n' "$2"; }
''' + 'case "${STATE}" in' + states)
    tools = tmp_path / 'envtool'
    tools.write_text('''#!/usr/bin/env python3
import os, sys
from pathlib import Path
p = Path(os.environ['ENV_FILE'])
env = dict(line.split('=', 1) for line in p.read_text().splitlines())
if Path(sys.argv[0]).name == 'fw_printenv':
    print(sys.argv[1] + '=' + env[sys.argv[1]])
else:
    if sys.argv[1] == '-s':
        env.update(line.split('=', 1) for line in sys.stdin.read().splitlines())
    else:
        env[sys.argv[1]] = sys.argv[2]
    p.write_text(''.join(k + '=' + v + '\\n' for k, v in env.items()))
''')
    tools.chmod(0o755)
    for name in ('fw_printenv', 'fw_setenv'):
        (tmp_path / name).symlink_to(tools)
    files = tmp_path / 'files'
    (files / 'tmp').mkdir(parents=True)
    for name in ('received-bootfit', 'received-rootfs'):
        (files / 'tmp' / name).touch()
    envfile = tmp_path / 'environment'

    def run(state, slot, root, original=None, upgrade='0'):
        envfile.write_text(f'mender_boot_part={slot}\nmender_boot_part_hex={slot}\nupgrade_available={upgrade}\nbootcount=0\n')
        if original:
            (files / 'tmp/original-slot').write_text(original)
        env = dict(os.environ, PATH=f'{tmp_path}:' + os.environ['PATH'],
                   ENV_FILE=str(envfile), ACTUAL_ROOT=root)
        result = subprocess.run(['sh', str(script), state, str(files)],
                                env=env, capture_output=True, text=True)
        values = dict(line.split('=', 1) for line in envfile.read_text().splitlines())
        return result, values
    return run


@pytest.mark.parametrize('active,passive', [('2', '3'), ('3', '2')])
def test_activation_switches_fit_and_rootfs_together(module, active, passive):
    result, env = module('ArtifactInstall', active, f'/dev/mmcblk0p{active}')
    assert result.returncode == 0, result.stderr
    assert env['mender_boot_part'] == env['mender_boot_part_hex'] == passive
    assert env['upgrade_available'] == '1'
    assert env['bootcount'] == '0'


@pytest.mark.parametrize('original,current', [('2', '3'), ('3', '2')])
def test_rollback_restores_both_selectors(module, original, current):
    result, env = module('ArtifactRollback', current, f'/dev/mmcblk0p{current}', original, '1')
    assert result.returncode == 0, result.stderr
    assert env['mender_boot_part'] == env['mender_boot_part_hex'] == original
    assert env['upgrade_available'] == '0'


@pytest.mark.parametrize('state', ['ArtifactVerifyReboot', 'ArtifactCommit'])
@pytest.mark.parametrize('root,success', [('/dev/mmcblk0p2', False), ('/dev/mmcblk0p3', True)])
def test_updated_slot_must_actually_be_booted(module, state, root, success):
    result, env = module(state, '3', root, '2', '1')
    assert (result.returncode == 0) == success, result.stderr
    if not success:
        assert 'does not match active slot' in result.stderr
        assert env['upgrade_available'] == '1'
