"""Exercise the actual pytest configuration in isolated child test suites."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def child_suite(tmp_path, source, *, required=None, changes=None, missing_mcp=False):
    project = tmp_path / "suite"
    project.mkdir()
    shutil.copyfile(ROOT / "tests/conftest.py", project / "conftest.py")
    shutil.copyfile(ROOT / "pyproject.toml", project / "pyproject.toml")
    (project / "test_sample.py").write_text(source)
    # This suite tests the pytest contract, not MCP execution. Its dependency
    # availability is explicit and independent of the parent suite's extras.
    if missing_mcp:
        (project / "sitecustomize.py").write_text("import sys\nsys.modules['mcp'] = None\n")
    else:
        (project / "mcp.py").write_text("# Dependency-discovery fixture only.\n")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST_", "RH_TEST_"))
    }
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = str(project)
    if required is not None:
        env.update(
            RH_TEST_REQUIRE_RUNTIME=required,
            RH_TEST_OMNIGENT_PYTHON=sys.executable,
            RH_TEST_STRATEGY_IMAGE="python@sha256:" + "a" * 64,
        )
    for key, value in (changes or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--basetemp", str(tmp_path / "pytest-tmp")],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_default_run_reports_skip_reason(tmp_path):
    result = child_suite(
        tmp_path,
        """
import pytest
def test_available():
    assert True
def test_optional_runtime():
    pytest.skip('Authored unavailable runtime')
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Runtime checks: optional" in result.stdout
    assert "1 passed, 1 skipped" in result.stdout
    assert "SKIPPED" in result.stdout and "Authored unavailable runtime" in result.stdout


@pytest.mark.parametrize("name", ["RH_TEST_STRATEGY_IMAGE", "RH_TEST_OMNIGENT_PYTHON"])
def test_required_run_refuses_missing_configuration_before_test_execution(tmp_path, name):
    result = child_suite(
        tmp_path,
        "def test_must_not_run():\n    assert False, 'TEST BODY RAN'\n",
        required="1",
        changes={name: None},
    )
    assert result.returncode == 4
    assert "Required runtime configuration is missing" in result.stderr
    assert name in result.stderr and "TEST BODY RAN" not in result.stdout


def test_required_run_refuses_missing_mcp_dependency(tmp_path):
    result = child_suite(
        tmp_path, "def test_ok():\n    assert True\n", required="1", missing_mcp=True
    )
    assert result.returncode == 4
    assert "MCP dependency" in result.stderr


def test_required_run_refuses_non_executable_runtime(tmp_path):
    python = tmp_path / "not-python"
    python.write_text("not executable")
    result = child_suite(
        tmp_path,
        "def test_ok():\n    assert True\n",
        required="1",
        changes={"RH_TEST_OMNIGENT_PYTHON": str(python)},
    )
    assert result.returncode == 4
    assert "executable Python file" in result.stderr


@pytest.mark.parametrize("value", ["true", ""])
def test_runtime_requirement_typo_is_an_error(tmp_path, value):
    result = child_suite(tmp_path, "def test_ok():\n    assert True\n", required=value)
    assert result.returncode == 4
    assert "must be 0 or 1" in result.stderr


@pytest.mark.parametrize(
    "source",
    [
        "import pytest\npytest.importorskip('rh_authored_missing_dependency', reason='Authored skip')\n",
        "import pytest\n@pytest.mark.skipif(True, reason='Authored skip')\ndef test_skipped():\n    pass\n",
        "import pytest\ndef test_skipped():\n    pytest.skip('Authored skip')\n",
        "import pytest\n@pytest.fixture\ndef resource():\n    yield\n    pytest.skip('Authored skip')\ndef test_skipped(resource):\n    assert True\n",
        "import pytest\ndef test_expected_failure():\n    pytest.xfail('Authored skip')\n",
    ],
)
def test_required_run_turns_collection_setup_call_teardown_and_xfail_skips_into_errors(
    tmp_path, source
):
    result = child_suite(tmp_path, source, required="1")
    assert result.returncode in {1, 2}, result.stdout + result.stderr
    assert "RH_TEST_REQUIRE_RUNTIME=1 forbids skipped checks" in result.stdout
    assert "Authored skip" in result.stdout
    assert "SKIPPED" not in result.stdout
    assert "Runtime checks: required" in result.stdout


def test_required_run_accepts_executed_checks(tmp_path):
    result = child_suite(tmp_path, "def test_ok():\n    assert 2 + 2 == 4\n", required="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Runtime checks: required" in result.stdout
    assert "1 passed" in result.stdout
