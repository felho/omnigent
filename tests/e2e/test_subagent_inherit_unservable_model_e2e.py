"""A bare Claude id an opencode worker cannot resolve must not be inherited.

Reproduces the reported journey. A user selects a Claude model
(``claude-opus-5``) for a claude-native orchestrator, then the orchestrator
fans out to an ``opencode-native`` worker via ``sys_session_send`` **without**
an explicit ``args.model``. A dispatch gate that only rejects a cross-*family*
id treats opencode as "multi-model, accept anything" and inherits the bare
``claude-opus-5`` id into the child's ``model_override``. opencode resolves a
model's provider from the id's own ``provider/`` prefix, so the synthesized
``opencode.json`` gets a bare ``claude-opus-5`` and the first turn dies with
``ProviderModelNotFoundError: Model not found: claude-opus-5/``.

Expected: a dispatch that names no model runs the child on opencode's own
default -- inheritance should *skip* when a bare id has nowhere to route rather
than push it through. So the child session must **not** be created with
``model_override == "claude-opus-5"``.

Fail -> pass contract: on the buggy build the child is created with
``model_override == "claude-opus-5"`` (the unresolvable inherited id), so the
assertion fails. Once inheritance is servability-aware the child keeps its own
default and the assertion passes.

(``pi`` is deliberately not exercised here: on the gateway path it routes a
claude id to its Databricks Anthropic surface (``/serving-endpoints/anthropic``
on the workspace host its credentials resolve), so an inherited claude id is
servable and must not be skipped -- unlike opencode's bare-id case.)

The claude-native brain is swapped for openai-agents against a mock LLM (the
standard mock-polly pattern from ``test_polly_e2e``); the opencode worker keeps
its NATIVE harness id, because the defect lives in how its id is inherited. The
plumbing under test -- CLI ``--model`` -> parent session -> ``sys_session_send``
(no args.model) -> ``_inherited_parent_model`` -> child session
``model_override`` -- is the real production path.

The opencode child is torn down shortly after creation (its native terminal
cannot boot on the unservable model here), so the child row is captured while
the dispatch is in flight rather than after the run settles.

Run::

    pytest tests/e2e/test_subagent_inherit_unservable_model_e2e.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.test_polly_e2e import _REPO, _mock_polly_spec_dir
from tests.e2e.test_polly_subagent_model_e2e import (
    _RUN_TIMEOUT_SEC,
    _api,
    local_polly_server,  # noqa: F401  (imported fixture)
)
from tests.e2e.test_subagent_model_inheritance_e2e import _run_env

# A Claude model the user selects for the claude-native parent session. A
# canonical vendor id with no ``provider/`` prefix -- opencode cannot resolve
# it against its own auth, so its own provider cannot serve it.
_SELECTED_MODEL = "claude-opus-5"


def _harness_bin_dir() -> str | None:
    """Locate the bootstrapped ``opencode`` CLI the dispatch preflight needs
    (``missing_harness_cli`` rejects a worker whose binary is missing or outside
    its version range), returning the dir to prepend to ``PATH``.

    Searches ``.harness-clis/node_modules/.bin`` up the worktree tree. The
    directory is prepended so the working binary wins over any wrapper shim
    earlier on ``PATH`` that crashes on ``--version``.
    """
    bases = [Path(_REPO), *Path(_REPO).parents, Path.cwd(), *Path.cwd().parents]
    seen: set[Path] = set()
    for base in bases:
        cand = base / ".harness-clis" / "node_modules" / ".bin"
        if cand in seen:
            continue
        seen.add(cand)
        if (cand / "opencode").exists():
            return str(cand)
    return None


def _observed_child_model_overrides(
    local_polly_server: str,  # noqa: F811  (imported fixture)
    mock_llm_server_url: str,
    tmp_path: Any,
    *,
    worker_agent: str,
) -> set[str | None]:
    """Drive the journey and return every ``model_override`` the dispatched
    worker child was observed to hold.

    The brain (openai-agents on the mock LLM) dispatches *worker_agent* with no
    ``args.model``; the worker keeps its native harness so the inheritance path
    under test fires. A background poller reads the child row while the run is
    in flight, because the opencode child is reaped soon after creation.

    :returns: The set of ``model_override`` values seen on the worker child. An
        empty set means the child never appeared (dispatch never created it).
    """
    from tests.e2e.conftest import configure_mock_llm, reset_mock_llm

    reset_mock_llm(mock_llm_server_url)
    polly_dir = _mock_polly_spec_dir(
        tmp_path,
        mock_llm_server_url,
        brain_model=_SELECTED_MODEL,
        rewrite_sub_agent_harnesses=False,
    )
    tag = uuid.uuid4().hex[:8]
    configure_mock_llm(
        mock_llm_server_url,
        [
            {
                "tool_calls": [
                    {
                        "call_id": f"call-{worker_agent}-{tag}",
                        "name": "sys_session_send",
                        "arguments": json.dumps(
                            {
                                "agent": worker_agent,
                                "title": f"{worker_agent}-explore",
                                "args": {
                                    "purpose": "explore",
                                    "input": "Report the first heading line of README.md.",
                                },
                            }
                        ),
                    }
                ]
            },
            {"text": f"Dispatched {worker_agent} without an explicit model."},
            {"text": "Turn complete."},
        ],
        key=_SELECTED_MODEL,
    )
    # The child draws from the default queue if it reaches a turn; keep it from
    # erroring the queue lookup if it does launch.
    configure_mock_llm(mock_llm_server_url, [{"text": "child done"}] * 3, key="default")

    env = _run_env(mock_llm_server_url)
    env["PATH"] = os.pathsep.join([_HARNESS_BIN_DIR, env.get("PATH", "")])

    overrides: set[str | None] = set()
    stop = threading.Event()

    def _poll() -> None:
        parent_id: str | None = None
        while not stop.is_set():
            try:
                if parent_id is None:
                    sessions = _api(local_polly_server, "/v1/sessions").get("data", [])
                    parents = [s["id"] for s in sessions if s.get("agent_name") == "polly"]
                    parent_id = parents[0] if parents else None
                if parent_id is not None:
                    kids = _api(
                        local_polly_server,
                        f"/v1/sessions/{parent_id}/child_sessions",
                    ).get("data", [])
                    for kid in kids:
                        if kid.get("tool") != worker_agent:
                            continue
                        child_id = kid.get("session_id") or kid.get("id")
                        if not child_id:
                            continue
                        # The list row omits model_override; the full row carries it.
                        full = _api(local_polly_server, f"/v1/sessions/{child_id}")
                        overrides.add(full.get("model_override"))
            except Exception:
                # Best-effort observer; the run result is authoritative.
                pass
            time.sleep(0.2)

    poller = threading.Thread(target=_poll, daemon=True)
    poller.start()
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "omnigent",
                "run",
                str(polly_dir),
                "--server",
                local_polly_server,
                "--model",
                _SELECTED_MODEL,
                "-p",
                f"Dispatch one read-only explore task to {worker_agent}.",
            ],
            cwd=str(_REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=_RUN_TIMEOUT_SEC,
        )
    finally:
        stop.set()
        poller.join(timeout=5)

    assert result.returncode == 0, (
        f"polly run exited {result.returncode}\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr[-2000:]}"
    )
    assert overrides, (
        f"no {worker_agent} child session was observed under the polly parent; "
        f"the dispatch never created it, so the inheritance path was not "
        f"exercised.\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr[-2000:]}"
    )
    return overrides


_HARNESS_BIN_DIR = _harness_bin_dir()

pytestmark = pytest.mark.skipif(
    _HARNESS_BIN_DIR is None,
    reason="opencode native harness CLI is not installed on PATH",
)


def test_opencode_worker_does_not_inherit_unservable_claude_model(
    local_polly_server: str,  # noqa: F811  (imported fixture)
    mock_llm_server_url: str,
    tmp_path: Any,
) -> None:
    """An opencode-native worker dispatched with no model must not inherit a
    bare Claude id it cannot serve.

    Without a servability check the gate inherits ``claude-opus-5`` into the
    child's ``model_override``; the synthesized ``opencode.json`` then pins a
    bare ``claude-opus-5`` (no ``provider/`` prefix) and opencode's first turn
    dies with ``ProviderModelNotFoundError: Model not found: claude-opus-5/``.
    The child should instead keep opencode's own configured default.
    """
    overrides = _observed_child_model_overrides(
        local_polly_server,
        mock_llm_server_url,
        tmp_path,
        worker_agent="opencode",
    )
    assert _SELECTED_MODEL not in overrides, (
        f"opencode-native worker dispatched without args.model inherited the "
        f"parent's Claude id {_SELECTED_MODEL!r} into its model_override -- "
        f"opencode requires a 'provider/model' id, so a bare {_SELECTED_MODEL!r} "
        f"makes its first turn fail with 'Model not found'. Inheritance should "
        f"skip an unservable id and let the worker keep its own default, but the "
        f"child session was created with model_override values {overrides!r}."
    )
