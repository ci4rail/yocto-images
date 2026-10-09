"""Check ownership of pending updates and use of the normal client settings."""
from types import SimpleNamespace
import shlex

import pytest

from yocto_tests.test_application_mender_update import MenderApplication


def test_client_uses_installed_configuration():
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return "installed\nCLIENT_EXIT=0\n"

    app = object.__new__(MenderApplication)
    app.dut = SimpleNamespace(run=run)
    app.pending = False
    app.client("install", "/data/test payload.mender")
    assert app.pending
    assert shlex.split(commands[0].splitlines()[0])[:3] == [
        "mender-update", "install", "/data/test payload.mender"]
    assert "MENDER_" not in commands[0]
    app.client("rollback")
    assert not app.pending


def test_failed_rollback_retains_pending_ownership():
    app = object.__new__(MenderApplication)
    app.dut = SimpleNamespace(run=lambda *args, **kwargs: "failed\nCLIENT_EXIT=1\n")
    app.pending = True
    with pytest.raises(AssertionError):
        app.client("rollback")
    assert app.pending

