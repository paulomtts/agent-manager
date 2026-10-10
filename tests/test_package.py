import tomllib
from pathlib import Path

import agent_manager


def test_package_imports():
    assert isinstance(agent_manager.__version__, str)


def test_version_is_the_one_pyproject_declares():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert agent_manager.__version__ == declared
