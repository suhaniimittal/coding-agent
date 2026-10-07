from __future__ import annotations

import subprocess

import pytest

from src.coding import test_runner


def test_detect_test_command_returns_none_when_no_marker(tmp_path):
    assert test_runner.detect_test_command(tmp_path) is None


def test_detect_test_command_matches_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n")
    assert test_runner.detect_test_command(tmp_path)[0] == "pytest"


def test_detect_test_command_matches_pom_xml_before_requirements(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    (tmp_path / "requirements.txt").write_text("")
    command = test_runner.detect_test_command(tmp_path)
    assert command[0] == "mvn"


def test_run_tests_reports_ran_false_when_no_command_detected(tmp_path):
    result = test_runner.run_tests(tmp_path)
    assert result.ran is False
    assert result.passed is False
    assert "no test command detected" in result.detail


def test_run_tests_reports_pass_on_real_success(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n")
    monkeypatch.setattr(
        test_runner,
        "_TEST_COMMANDS_BY_MARKER",
        [("pyproject.toml", ["python3", "-c", "print('ok')"])],
    )

    result = test_runner.run_tests(tmp_path)

    assert result.ran is True
    assert result.passed is True
    assert "ok" in result.detail


def test_run_tests_reports_failure_with_real_output(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n")
    monkeypatch.setattr(
        test_runner,
        "_TEST_COMMANDS_BY_MARKER",
        [("pyproject.toml", ["python3", "-c", "import sys; print('boom'); sys.exit(1)"])],
    )

    result = test_runner.run_tests(tmp_path)

    assert result.ran is True
    assert result.passed is False
    assert "boom" in result.detail


def test_run_tests_reports_timeout(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n")
    monkeypatch.setattr(
        test_runner,
        "_TEST_COMMANDS_BY_MARKER",
        [("pyproject.toml", ["python3", "-c", "import time; time.sleep(5)"])],
    )

    result = test_runner.run_tests(tmp_path, timeout=1)

    assert result.ran is True
    assert result.passed is False
    assert "timed out" in result.detail


def test_run_tests_reports_missing_executable_without_crashing(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n")
    monkeypatch.setattr(
        test_runner,
        "_TEST_COMMANDS_BY_MARKER",
        [("pyproject.toml", ["this-binary-does-not-exist-xyz"])],
    )

    result = test_runner.run_tests(tmp_path)

    assert result.ran is False
    assert result.passed is False
    assert "could not run" in result.detail


def test_run_tests_does_not_pass_secrets_to_the_repos_test_process(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("")
    for name in ("GITHUB_TOKEN", "NEO4J_PASSWORD", "OPENAI_API_KEY", "AGENTS_GATEWAY_KEY",
                 "AWS_ROLE_ARN", "STORAGE_SECRET_KEY", "TENANT_ID"):
        monkeypatch.setenv(name, "secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    test_runner.run_tests(tmp_path)

    assert seen["env"]["PATH"] == "/usr/bin"
    for name in ("GITHUB_TOKEN", "NEO4J_PASSWORD", "OPENAI_API_KEY", "AGENTS_GATEWAY_KEY",
                 "AWS_ROLE_ARN", "STORAGE_SECRET_KEY", "TENANT_ID"):
        assert name not in seen["env"]
