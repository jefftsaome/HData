"""pytest 配置"""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="运行需要真实 Chrome 的集成测试",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: 需要真实 Chrome 的集成测试")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-integration"):
        skip = pytest.mark.skip(reason="需要 --run-integration 参数")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)
