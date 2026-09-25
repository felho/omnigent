"""``build_native_relay_tool_schemas`` must not depend on the process cwd existing.

A runner can outlive its process cwd when the session's git worktree is
removed while the runner lives on. Schema extraction never executes tools, so
it must keep working — and keep returning the same ``sys_os_*`` schemas — even
though ``os.getcwd()`` / ``Path.cwd()`` raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnigent.runner.tool_dispatch import build_native_relay_tool_schemas
from omnigent.spec.types import AgentSpec, OSEnvSpec

_OS_ENV_TOOL_NAMES = {"sys_os_read", "sys_os_write", "sys_os_edit", "sys_os_shell"}


def _os_env_schemas(spec: AgentSpec | None) -> dict[str, object]:
    return {
        s["name"]: s
        for s in build_native_relay_tool_schemas(spec)
        if s["name"] in _OS_ENV_TOOL_NAMES
    }


@pytest.mark.posix_only
def test_native_relay_os_schemas_survive_deleted_cwd_with_no_spec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``spec=None`` still relays ``sys_os_*`` schemas once the cwd is gone."""
    baseline = _os_env_schemas(None)
    assert baseline.keys() == _OS_ENV_TOOL_NAMES

    removed = tmp_path / "removed"
    removed.mkdir()
    with monkeypatch.context() as context:
        context.chdir(removed)
        removed.rmdir()
        relayed = _os_env_schemas(None)

    assert relayed == baseline


@pytest.mark.posix_only
def test_native_relay_os_schemas_survive_deleted_cwd_with_spec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A spec whose ``os_env`` has no explicit ``cwd`` also survives a deleted cwd.

    ``ToolManager`` would otherwise resolve the ``None`` cwd via
    ``os.getcwd()``, which the runner's process cwd can't provide once its
    session worktree is removed.
    """
    spec = AgentSpec(spec_version=1, os_env=OSEnvSpec())
    baseline = _os_env_schemas(spec)
    assert baseline.keys() == _OS_ENV_TOOL_NAMES

    removed = tmp_path / "removed"
    removed.mkdir()
    with monkeypatch.context() as context:
        context.chdir(removed)
        removed.rmdir()
        relayed = _os_env_schemas(spec)

    assert relayed == baseline


@pytest.mark.posix_only
def test_native_relay_os_schemas_keep_explicit_spec_cwd_when_process_cwd_is_deleted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A spec with an explicit, still-existing ``os_env.cwd`` is left untouched."""
    explicit_cwd = tmp_path / "workspace"
    explicit_cwd.mkdir()
    spec = AgentSpec(spec_version=1, os_env=OSEnvSpec(cwd=str(explicit_cwd)))

    removed = tmp_path / "removed"
    removed.mkdir()
    with monkeypatch.context() as context:
        context.chdir(removed)
        removed.rmdir()
        relayed = _os_env_schemas(spec)

    assert relayed.keys() == _OS_ENV_TOOL_NAMES
