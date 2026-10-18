def pytest_configure(config):
    config.addinivalue_line(
        "markers", "offline_unit: cheap fixture or pure decision test without subprocesses")
