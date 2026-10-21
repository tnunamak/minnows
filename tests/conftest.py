import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "offline_unit: cheap fixture or pure decision test without subprocesses")


@pytest.fixture(autouse=True)
def isolate_model_policy_state(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'xdg-state'))
