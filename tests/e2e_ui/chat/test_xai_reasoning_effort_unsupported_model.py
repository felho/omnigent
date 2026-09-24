"""E2E: a reasoning-enabled turn on xai/grok-4 completes without an HTTP 400.

xAI accepts ``reasoning_effort`` on only a few Grok models; ``grok-4``
rejects it with HTTP 400 "Argument not supported on this model:
reasoning_effort". Omnigent forwards the session's reasoning effort into the
Chat Completions request body for every model unconditionally, so a
reasoning-configured ``xai/grok-4`` session fails every turn. The mock LLM
stands in for the xAI endpoint (no xAI credentials/egress in CI) and mirrors
that contract via ``reject_params``: the request succeeds iff the body omits
``reasoning_effort``.

Journey:

1. configure an agent on ``xai/grok-4`` (openai-agents harness, on the
   chat-completions wire an xAI provider uses -- xAI has no /responses)
2. start a session with reasoning effort "medium"
3. send a message
4. the turn must complete with an assistant reply -- while the bug is live
   the body carries ``reasoning_effort``, the xAI-faithful endpoint rejects
   it, and the session shows an error pill instead.

Run::

    pytest tests/e2e_ui/chat/test_xai_reasoning_effort_unsupported_model.py
"""

from __future__ import annotations

import contextlib
import io
import json
import tarfile
import uuid
from collections.abc import Iterator

import httpx
import pytest
from playwright.sync_api import Page, expect

from tests.e2e_ui.conftest import _server_state, configure_mock_llm

_COMPOSER = "Send a message…"
_ASSISTANT = '[data-testid="message-bubble"][data-role="assistant"]'
_ERROR_PILL = '[data-testid="error-pill"]'

# The scripted grok-4 reply; visible in the transcript only when the request
# body omitted ``reasoning_effort`` (the mock rejects it otherwise).
_REPLY = "Hello from grok-4 without reasoning_effort."

# Ceiling for the turn to settle either way; the first turn includes the
# harness subprocess boot.
_TURN_SETTLE_TIMEOUT_MS = 120_000

# ``use_responses: false`` pins the chat-completions wire, matching what a
# configured xAI provider uses (xAI serves no OpenAI /responses endpoint).
# Pin the mock endpoint so a configured default provider cannot reroute the turn.
_AGENT_YAML = """\
name: {name}
prompt: You are a helpful assistant. Answer briefly.

executor:
  model: xai/grok-4
  harness: openai-agents
  use_responses: false
  auth:
    type: api_key
    api_key: mock-key
    base_url: {mock_base_url}
"""


def _agent_bundle(name: str, mock_base_url: str) -> bytes:
    """Gzip-tar the inline agent YAML for multipart upload."""
    yaml_text = _AGENT_YAML.format(name=name, mock_base_url=mock_base_url)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = yaml_text.encode()
        info = tarfile.TarInfo(name=f"{name}.yaml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def grok_reasoning_session(
    live_server: str, mock_llm_server_url: str
) -> Iterator[tuple[str, str]]:
    """Create a reasoning-enabled xai/grok-4 session bound to the shared runner.

    :param live_server: Spawned server base URL.
    :returns: ``(base_url, session_id)``.
    """
    name = f"grok_reason_{uuid.uuid4().hex[:8]}"
    create_resp = httpx.post(
        f"{live_server}/v1/sessions",
        data={"metadata": json.dumps({"reasoning_effort": "medium"})},
        files={
            "bundle": (
                "agent.tar.gz",
                _agent_bundle(name, f"{mock_llm_server_url}/v1"),
                "application/gzip",
            )
        },
        timeout=30.0,
    )
    if create_resp.status_code >= 400:
        raise RuntimeError(
            f"session create failed ({create_resp.status_code}): {create_resp.text}"
        )
    session_id = create_resp.json()["session_id"]
    try:
        httpx.patch(
            f"{live_server}/v1/sessions/{session_id}",
            json={"runner_id": str(_server_state["runner_id"])},
            timeout=10.0,
        ).raise_for_status()
        yield (live_server, session_id)
    finally:
        httpx.delete(f"{live_server}/v1/sessions/{session_id}", timeout=10.0)


def _grok_wire_requests(mock_url: str, token: str) -> list[dict]:
    """Return captured chat-completions bodies whose user text carries *token*."""
    resp = httpx.get(f"{mock_url}/mock/requests", timeout=5.0)
    resp.raise_for_status()
    requests = resp.json()["requests"]
    return [r for r in requests if isinstance(r, dict) and token in json.dumps(r)]


def test_reasoning_turn_on_xai_grok4_succeeds_without_reasoning_effort(
    page: Page,
    grok_reasoning_session: tuple[str, str],
    mock_llm_server_url: str,
) -> None:
    """A medium-effort turn on xai/grok-4 must not be rejected with HTTP 400.

    Regression test: ``reasoning_effort`` was forwarded to every Grok model,
    and models that don't accept it (grok-4, grok-code-fast-1,
    grok-4-fast-reasoning) rejected the whole turn with HTTP 400
    "Argument not supported on this model: reasoning_effort".
    """
    base_url, session_id = grok_reasoning_session
    token = f"grok-effort-{uuid.uuid4().hex[:6]}"

    # An xAI-faithful grok-4 queue: every entry rejects any request whose
    # body carries ``reasoning_effort`` and answers normally otherwise.
    # Several identical entries so a client-side retry of the 400 still hits
    # the same contract instead of draining into the permissive default.
    configure_mock_llm(
        mock_llm_server_url,
        [{"text": _REPLY, "reject_params": ["reasoning_effort"]}] * 4,
        key="xai/grok-4",
        match=token,
    )

    page.goto(f"{base_url}/c/{session_id}")
    composer = page.get_by_placeholder(_COMPOSER)
    expect(composer).to_be_visible()

    composer.fill(f"Say hello ({token})")
    page.get_by_role("button", name="Send", exact=True).click()

    # Wait for the turn to settle either way: assistant reply (fixed) or
    # error pill (bug live), then let the final state render.
    page.wait_for_selector(
        f"{_ASSISTANT}, {_ERROR_PILL}",
        timeout=_TURN_SETTLE_TIMEOUT_MS,
    )
    page.wait_for_timeout(1_500)

    wire_requests = _grok_wire_requests(mock_llm_server_url, token)
    offending = [r for r in wire_requests if r.get("reasoning_effort") is not None]

    pills = page.locator(_ERROR_PILL)
    if pills.count() > 0:
        # Expand the pill so the failure detail (the provider's 400 message)
        # is visible in the transcript and in any recording of this run.
        with contextlib.suppress(Exception):
            pills.first.click()
            page.wait_for_timeout(1_500)
        pill_text = pills.first.inner_text()
        raise AssertionError(
            f"turn on xai/grok-4 with reasoning effort 'medium' failed: {pill_text!r}; "
            f"{len(offending)}/{len(wire_requests)} captured request bodies carried "
            f"reasoning_effort (it must be omitted for grok models that reject it)"
        )

    expect(page.get_by_text(_REPLY).first).to_be_visible()

    # The ticket's contract, asserted on the wire: grok models that reject
    # ``reasoning_effort`` must not receive it.
    assert not offending, f"request body still carried reasoning_effort: {offending[0]}"
