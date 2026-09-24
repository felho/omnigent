"""Dispatch-gate tests for parent-model inheritance on sub-agent creation.

A ``sys_session_send`` that names no ``args.model`` must not silently land
the child on the worker/provider default when the user selected a model for
the parent session: the gate reads the parent's effective model
(``model_override`` falling back to ``llm_model``) and persists it as the
child's ``model_override``. Inheritance is best-effort and skips quietly
when the sub-agent spec pins its own model, the child harness has no
override plumbing, the parent model's family cannot run on the child
harness, an opencode child cannot resolve a bare id (no ``provider/``
prefix, no synthesizing Databricks profile), or the parent snapshot is
unavailable.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest


def _spec_with_worker(
    harness: str,
    *,
    worker_model: str | None = None,
    worker_profile: str | None = None,
) -> SimpleNamespace:
    """
    Build a parent-spec stub declaring one ``worker`` sub-agent.

    :param harness: The sub-agent's declared harness, e.g. ``"claude-sdk"``.
    :param worker_model: Optional ``executor.model`` pin on the worker spec.
    :param worker_profile: Optional ``executor.config.profile`` on the worker.
    :returns: A structural parent-spec stub for ``execute_tool``.
    """
    config: dict[str, Any] = {"harness": harness}
    if worker_profile is not None:
        config["profile"] = worker_profile
    executor = SimpleNamespace(type="omnigent", config=config)
    if worker_model is not None:
        executor.model = worker_model
    return SimpleNamespace(sub_agents=[SimpleNamespace(name="worker", executor=executor)])


def _stub_opencode_launchable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let an opencode-native dispatch pass preflight in a CLI-less test env.

    The dispatch rejects a worker whose harness CLI is missing; stub that probe
    present (the inheritance gate is under test, not the binary) and clear the
    ambient Databricks profile so the opencode servability rule reads the spec
    alone.

    :param monkeypatch: Pytest monkeypatch fixture.
    """
    from omnigent.onboarding import harness_install

    monkeypatch.setattr(harness_install, "missing_harness_cli", lambda _harness: None)
    monkeypatch.delenv("DATABRICKS_CONFIG_PROFILE", raising=False)


async def _dispatch_without_model(
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent_spec: Any,
    conv_id: str,
    parent_snapshot: dict[str, Any] | None,
    explicit_model: str | None = None,
) -> list[dict[str, Any]]:
    """
    Drive one fresh-create ``sys_session_send`` and capture create bodies.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param agent_spec: The parent spec under test.
    :param conv_id: Unique parent conversation id per test.
    :param parent_snapshot: JSON the mock server returns for
        ``GET /v1/sessions/{conv_id}``; ``None`` serves a 404.
    :param explicit_model: Optional ``args.model`` for the dispatch.
    :returns: The captured ``POST /v1/sessions`` bodies.
    """
    from omnigent.runner import app as runner_app
    from omnigent.runner.tool_dispatch import execute_tool

    create_bodies: list[dict[str, Any]] = []
    monkeypatch.setattr(runner_app, "get_session_agent_id", lambda _sid: "ag_parent")
    monkeypatch.setattr(runner_app, "register_child_session", lambda *a, **k: None)
    session_inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def _server_handler(request: httpx.Request) -> httpx.Response:
        """Serve the parent snapshot, child lookup, create, and message POSTs."""
        if request.method == "GET" and request.url.path == f"/v1/sessions/{conv_id}":
            if parent_snapshot is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json=parent_snapshot)
        if (
            request.method == "GET"
            and request.url.path == f"/v1/sessions/{conv_id}/child_sessions"
        ):
            return httpx.Response(200, json={"data": []})
        if request.method == "POST" and request.url.path == "/v1/sessions":
            create_bodies.append(json.loads(request.content))
            return httpx.Response(201, json={"id": "conv_child_inherit"})
        if (
            request.method == "POST"
            and request.url.path == "/v1/sessions/conv_child_inherit/events"
        ):
            return httpx.Response(202, json={"queued": True})
        return httpx.Response(404, json={"error": str(request.url)})

    args: dict[str, Any] = {"input": "do the task"}
    if explicit_model is not None:
        args["model"] = explicit_model
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_server_handler),
        base_url="http://server",
    ) as server_client:
        try:
            output = await execute_tool(
                tool_name="sys_session_send",
                arguments=json.dumps({"agent": "worker", "title": "task", "args": args}),
                server_client=server_client,
                conversation_id=conv_id,
                agent_spec=agent_spec,
                session_inbox=session_inbox,
            )
        finally:
            runner_app.unregister_subagent_work("conv_child_inherit")
            runner_app._session_inboxes_ref.pop(conv_id, None)
    payload = json.loads(output)
    assert payload["status"] == "launching", output
    assert len(create_bodies) == 1, "fresh named send must create exactly one child"
    return create_bodies


@pytest.mark.asyncio
async def test_child_inherits_parent_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A no-model dispatch persists the parent's ``model_override`` on the child.

    :param monkeypatch: Pytest monkeypatch fixture.
    """
    bodies = await _dispatch_without_model(
        monkeypatch,
        agent_spec=_spec_with_worker("claude-sdk"),
        conv_id="conv_parent_inherit_override",
        parent_snapshot={
            "id": "conv_parent_inherit_override",
            "agent_id": "ag_parent",
            "model_override": "databricks-claude-sonnet-4-6",
            "llm_model": "databricks-claude-opus-4-8",
        },
    )
    assert bodies[0]["model_override"] == "databricks-claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_child_inherits_parent_llm_model_when_no_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Without a per-session override, the parent's effective ``llm_model``
    (e.g. the CLI ``--model`` selection baked into the spec) is inherited.

    :param monkeypatch: Pytest monkeypatch fixture.
    """
    bodies = await _dispatch_without_model(
        monkeypatch,
        agent_spec=_spec_with_worker("claude-sdk"),
        conv_id="conv_parent_inherit_llm",
        parent_snapshot={
            "id": "conv_parent_inherit_llm",
            "agent_id": "ag_parent",
            "model_override": None,
            "llm_model": "databricks-claude-sonnet-4-6",
        },
    )
    assert bodies[0]["model_override"] == "databricks-claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_explicit_dispatch_model_wins_over_parent_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    An explicit ``args.model`` is persisted verbatim; the parent's own
    selection never overrides the caller's request.

    :param monkeypatch: Pytest monkeypatch fixture.
    """
    bodies = await _dispatch_without_model(
        monkeypatch,
        agent_spec=_spec_with_worker("claude-sdk"),
        conv_id="conv_parent_explicit_wins",
        parent_snapshot={
            "id": "conv_parent_explicit_wins",
            "agent_id": "ag_parent",
            "model_override": "databricks-claude-sonnet-4-6",
            "llm_model": None,
        },
        explicit_model="databricks-claude-haiku-4-5",
    )
    assert bodies[0]["model_override"] == "databricks-claude-haiku-4-5"


@pytest.mark.asyncio
async def test_worker_spec_model_pin_blocks_inheritance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A worker whose spec pins ``executor.model`` keeps its own model: the
    create body carries no ``model_override`` and the pinned spec model
    resolves at child boot.

    :param monkeypatch: Pytest monkeypatch fixture.
    """
    bodies = await _dispatch_without_model(
        monkeypatch,
        agent_spec=_spec_with_worker("claude-sdk", worker_model="databricks-claude-haiku-4-5"),
        conv_id="conv_parent_spec_pin",
        parent_snapshot={
            "id": "conv_parent_spec_pin",
            "agent_id": "ag_parent",
            "model_override": "databricks-claude-sonnet-4-6",
            "llm_model": None,
        },
    )
    assert "model_override" not in bodies[0]


@pytest.mark.asyncio
async def test_family_mismatch_blocks_inheritance_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A Claude parent selection is not forced onto a codex-family worker: the
    dispatch still succeeds, with no ``model_override`` on the child.

    :param monkeypatch: Pytest monkeypatch fixture.
    """
    bodies = await _dispatch_without_model(
        monkeypatch,
        agent_spec=_spec_with_worker("codex"),
        conv_id="conv_parent_family_mismatch",
        parent_snapshot={
            "id": "conv_parent_family_mismatch",
            "agent_id": "ag_parent",
            "model_override": "databricks-claude-sonnet-4-6",
            "llm_model": None,
        },
    )
    assert "model_override" not in bodies[0]


@pytest.mark.asyncio
async def test_unplumbed_harness_blocks_inheritance_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A child harness without model-override plumbing skips inheritance
    (a persisted value would be silently ignored) without failing the
    dispatch — unlike an explicit ``args.model``, which fails loud.

    :param monkeypatch: Pytest monkeypatch fixture.
    """
    bodies = await _dispatch_without_model(
        monkeypatch,
        agent_spec=_spec_with_worker("unknown-harness"),
        conv_id="conv_parent_unplumbed_inherit",
        parent_snapshot={
            "id": "conv_parent_unplumbed_inherit",
            "agent_id": "ag_parent",
            "model_override": "databricks-claude-sonnet-4-6",
            "llm_model": None,
        },
    )
    assert "model_override" not in bodies[0]


@pytest.mark.asyncio
async def test_no_parent_model_leaves_child_on_worker_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A parent with no model selection dispatches a child with no override —
    the worker resolves its own default, exactly as before.

    :param monkeypatch: Pytest monkeypatch fixture.
    """
    bodies = await _dispatch_without_model(
        monkeypatch,
        agent_spec=_spec_with_worker("claude-sdk"),
        conv_id="conv_parent_no_model",
        parent_snapshot={
            "id": "conv_parent_no_model",
            "agent_id": "ag_parent",
            "model_override": None,
            "llm_model": None,
        },
    )
    assert "model_override" not in bodies[0]


@pytest.mark.asyncio
async def test_unreachable_parent_snapshot_skips_inheritance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A failed parent-snapshot read degrades to no inheritance — the dispatch
    itself must not fail on the best-effort lookup.

    :param monkeypatch: Pytest monkeypatch fixture.
    """
    bodies = await _dispatch_without_model(
        monkeypatch,
        agent_spec=_spec_with_worker("claude-sdk"),
        conv_id="conv_parent_snapshot_404",
        parent_snapshot=None,
    )
    assert "model_override" not in bodies[0]


@pytest.mark.asyncio
async def test_opencode_worker_skips_bare_id_inheritance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A bare vendor id is not inherited onto an opencode worker: opencode
    resolves a model's provider from its ``provider/`` prefix, so the bare id
    would land verbatim in ``opencode.json`` and fail the first turn with
    ``ProviderModelNotFoundError``. The child keeps its own default.

    :param monkeypatch: Pytest monkeypatch fixture.
    """
    _stub_opencode_launchable(monkeypatch)
    bodies = await _dispatch_without_model(
        monkeypatch,
        agent_spec=_spec_with_worker("opencode-native"),
        conv_id="conv_parent_opencode_bare_id",
        parent_snapshot={
            "id": "conv_parent_opencode_bare_id",
            "agent_id": "ag_parent",
            "model_override": "claude-opus-5",
            "llm_model": None,
        },
    )
    assert "model_override" not in bodies[0]


@pytest.mark.asyncio
async def test_opencode_worker_inherits_provider_prefixed_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A ``provider/model`` id is inherited onto an opencode worker verbatim —
    opencode resolves the provider from the prefix against its own auth.

    :param monkeypatch: Pytest monkeypatch fixture.
    """
    _stub_opencode_launchable(monkeypatch)
    bodies = await _dispatch_without_model(
        monkeypatch,
        agent_spec=_spec_with_worker("opencode-native"),
        conv_id="conv_parent_opencode_prefixed",
        parent_snapshot={
            "id": "conv_parent_opencode_prefixed",
            "agent_id": "ag_parent",
            "model_override": "anthropic/claude-sonnet-4-5",
            "llm_model": None,
        },
    )
    assert bodies[0]["model_override"] == "anthropic/claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_opencode_worker_with_profile_inherits_gateway_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A worker spec naming a Databricks profile keeps inheriting: the launch
    synthesizes a gateway provider that pins the id's serving endpoint.

    :param monkeypatch: Pytest monkeypatch fixture.
    """
    _stub_opencode_launchable(monkeypatch)
    bodies = await _dispatch_without_model(
        monkeypatch,
        agent_spec=_spec_with_worker("opencode-native", worker_profile="oss"),
        conv_id="conv_parent_opencode_profile",
        parent_snapshot={
            "id": "conv_parent_opencode_profile",
            "agent_id": "ag_parent",
            "model_override": "databricks-claude-opus-4-8",
            "llm_model": None,
        },
    )
    assert bodies[0]["model_override"] == "databricks-claude-opus-4-8"


@pytest.mark.asyncio
async def test_pi_worker_inherits_claude_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A claude id IS inherited onto a pi worker: pi routes a validated id to the
    provider its launch configured (see ``_pi_provider_for_model``), so the
    dispatch gate does not second-guess servability. Normalization localizes
    the bare id for the child's resolved provider.

    :param monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("DATABRICKS_CONFIG_PROFILE", raising=False)
    bodies = await _dispatch_without_model(
        monkeypatch,
        agent_spec=_spec_with_worker("pi"),
        conv_id="conv_parent_pi_inherits",
        parent_snapshot={
            "id": "conv_parent_pi_inherits",
            "agent_id": "ag_parent",
            "model_override": "databricks-claude-opus-5",
            "llm_model": None,
        },
    )
    assert bodies[0]["model_override"] == "databricks-claude-opus-5"
