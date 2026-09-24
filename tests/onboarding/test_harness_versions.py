"""Installed CLI version discovery with real isolated executables."""

import subprocess


def test_cli_versions_use_real_binary_and_refresh_after_upgrade(tmp_path, monkeypatch):
    from omnigent.onboarding import harness_install as install

    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\necho '2.1.0 (Claude Code)'\n")
    binary.chmod(0o755)
    monkeypatch.setattr(
        install, "resolve_cli_binary", lambda name: str(binary) if name == "claude" else None
    )
    versions = install.harness_cli_versions()
    assert versions["claude-native"] == "2.1.0"
    assert versions["native-claude"] == "2.1.0"
    assert "claude-sdk" not in versions
    assert "codex-native" not in versions
    binary.write_text("#!/bin/sh\necho '2.123.0 (Claude Code)'\n")
    assert install.harness_cli_versions()["claude-native"] == "2.123.0"
    binary.write_text("#!/bin/sh\necho 'not a version'\n")
    assert "claude-native" not in install.harness_cli_versions()


def test_cli_version_probe_passes_timeout(monkeypatch):
    from omnigent.onboarding import harness_install as install

    monkeypatch.setattr(install, "resolve_cli_binary", lambda name: "/missing/cli")

    def hung(*args, **kwargs):
        assert kwargs["timeout"] == 0.01
        raise subprocess.TimeoutExpired(args[0], 0.01)

    monkeypatch.setattr(install.subprocess, "run", hung)
    assert install.harness_cli_version("anthropic", timeout=0.01)[0] is None
