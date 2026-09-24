"""Children are not spawned onto a builtin ACP CLI agent this host cannot run.

The server seeds one built-in agent per ACP CLI catalog row unconditionally,
because only the executing host knows whether the vendor CLI is installed. A
child made with ``sys_session_create`` inherits the caller's runner, so it never
passes the host's launch-time readiness gate: an orchestrator that picked such
an agent got a child whose every turn failed at subprocess start. The runner
now keeps those agents out of ``sys_agent_list`` and refuses them at create.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omnigent.acp_cli_harnesses import ACP_CLI_HARNESSES, AcpCliHarness
from omnigent.db.utils import builtin_agent_id
from omnigent.harness_install_spec import HarnessInstallSpec
from omnigent.runner.tool_dispatch import execute_tool

_FAKE_ROW = AcpCliHarness(
    install=HarnessInstallSpec(
        "Fake CLI",
        "fakecli",
        None,
        install_hint="curl -fsSL https://fake.example/install.sh | bash",
    ),
    args=("acp",),
)

_BUILTINS = [
    {"id": builtin_agent_id("fakecli"), "name": "fakecli", "harness": "fakecli"},
    {"id": "ag_claude", "name": "claude-native-ui", "harness": "claude-native"},
    {"id": "ag_unknown", "name": "mystery", "harness": None},
]


@pytest.fixture
def fake_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register a fake ACP CLI row with no env or config path override."""
    monkeypatch.setitem(ACP_CLI_HARNESSES, "fakecli", _FAKE_ROW)
    monkeypatch.delenv("OMNIGENT_FAKECLI_PATH", raising=False)
    monkeypatch.setattr("omnigent.runtime.workflow.load_config", dict)


def _cli_installed(monkeypatch: pytest.MonkeyPatch, installed: bool) -> None:
    monkeypatch.setattr(
        "omnigent._platform.resolve_cli_binary",
        lambda name, **_: "/usr/local/bin/fakecli" if installed and name == "fakecli" else None,
    )


async def _call(
    tool_name: str,
    arguments: dict[str, Any],  # type: ignore[explicit-any]
    seen: list[str],
) -> dict[str, Any]:  # type: ignore[explicit-any]
    async def _serve(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v1/agents":
            return httpx.Response(200, json={"data": _BUILTINS})
        if request.method == "POST" and request.url.path == "/v1/sessions":
            body = json.loads(request.content)
            return httpx.Response(
                201,
                json={"id": "conv_child", "agent_id": body["agent_id"], "status": "idle"},
            )
        if request.url.path == "/v1/sessions":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404, json={"error": str(request.url)})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_serve), base_url="http://server"
    ) as server_client:
        output = await execute_tool(
            tool_name=tool_name,
            arguments=json.dumps(arguments),
            server_client=server_client,
            conversation_id="conv_parent",
        )
    return json.loads(output)


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake_row")
async def test_agent_list_omits_acp_cli_agent_whose_cli_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cli_installed(monkeypatch, installed=False)
    listing = await _call("sys_agent_list", {}, [])
    # Only the ACP CLI row is judged: native and harness-less rows are untouched.
    assert [row["name"] for row in listing["builtins"]] == ["claude-native-ui", "mystery"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake_row")
async def test_agent_list_keeps_acp_cli_agent_once_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cli_installed(monkeypatch, installed=True)
    listing = await _call("sys_agent_list", {}, [])
    assert [row["name"] for row in listing["builtins"]] == [
        "fakecli",
        "claude-native-ui",
        "mystery",
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake_row")
async def test_agent_list_honors_a_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _cli_installed(monkeypatch, installed=False)
    monkeypatch.setenv("OMNIGENT_FAKECLI_PATH", "/opt/fakecli/bin/fakecli")
    listing = await _call("sys_agent_list", {}, [])
    assert "fakecli" in [row["name"] for row in listing["builtins"]]


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake_row")
async def test_session_create_refuses_missing_acp_cli_without_creating_a_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cli_installed(monkeypatch, installed=False)
    seen: list[str] = []
    result = await _call(
        "sys_session_create",
        {"agent_id": builtin_agent_id("fakecli"), "message": "go"},
        seen,
    )
    assert result["error"] == "harness_not_installed"
    assert result["harness"] == "fakecli"
    assert "`fakecli`" in result["detail"]
    # Refused before the create: no child session exists to fail its first turn.
    assert "POST /v1/sessions" not in seen


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake_row")
async def test_session_create_spawns_installed_acp_cli_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cli_installed(monkeypatch, installed=True)
    seen: list[str] = []
    result = await _call("sys_session_create", {"agent_id": builtin_agent_id("fakecli")}, seen)
    assert result["conversation_id"] == "conv_child"
    assert "POST /v1/sessions" in seen


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake_row")
async def test_session_create_does_not_judge_other_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unexpected(*_args: object, **_kwargs: object) -> str | None:
        raise AssertionError("only ACP CLI built-ins are resolved")

    monkeypatch.setattr("omnigent.runtime.workflow.resolve_acp_cli_executable", _unexpected)
    seen: list[str] = []
    result = await _call("sys_session_create", {"agent_id": "ag_claude"}, seen)
    assert result["conversation_id"] == "conv_child"
    assert "POST /v1/sessions" in seen
