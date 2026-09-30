import sys
from pathlib import Path

import pytest

from yocto_tests.station import Station, load_config


def pytest_addoption(parser):
    parser.addoption("--station", help="Station YAML configuration")
    parser.addoption("--image", help="Local TEZI tar path, overrides configuration")
    parser.addoption("--skip-flash", action="store_true", help="Reuse the installed target image")
    parser.addoption("--results", default="results", help="Directory for station logs")


def pytest_configure(config):
    if sys.prefix == sys.base_prefix:
        raise pytest.UsageError("Run pytest inside a virtual environment, e.g. tests/run.sh")


@pytest.fixture(scope="session")
def station(request):
    from yocto_tests.setup import prepare
    path = request.config.getoption("--station")
    if not path:
        pytest.fail("Hardware execution requires --station; use --collect-only to inspect tests")
    cfg = load_config(path)
    if request.config.getoption("--image"):
        cfg["image_file"] = request.config.getoption("--image")
    s = Station(cfg, request.config.getoption("--results"))
    with prepare(s, skip_flash=request.config.getoption("--skip-flash")):
        yield s


@pytest.fixture
def feature(station):
    def require(name):
        if name not in station.cfg.get("features", []):
            pytest.skip(f"Station does not declare {name}")
    return require


@pytest.fixture(scope="session")
def baseline():
    import yaml
    return yaml.safe_load((Path(__file__).parent / "config/security_baseline.yaml").read_text())["security_baseline"]


@pytest.fixture(scope="session")
def gps_client(station):
    from yocto_tests.setup import install_gps_client
    if "gnss" not in station.cfg.get("features", []):
        pytest.skip("Station does not declare gnss")
    install_gps_client(station, station.work)
    return station.cfg["gpsdclient"]
