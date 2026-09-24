"""Self-tests for the reusable browser-only chat backend."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.browser_ui.chat.session_contract import ChatSessionContract, model_option


def test_contract_drives_history_catalog_and_live_status(
    page: Page,
    chat_session_contract: ChatSessionContract,
) -> None:
    """Exercise the fixture features later chat slices depend on."""
    chat = chat_session_contract
    chat.seed_transcript(24)
    chat.set_catalog(
        harness="claude",
        models=[
            model_option("sonnet", display_name="Sonnet", is_default=True),
            model_option("opus", display_name="Opus"),
        ],
        selected_model="sonnet",
    )
    page.goto(chat.url)

    expect(page.get_by_text("Request 24", exact=False)).to_be_visible(timeout=20_000)
    expect(page.get_by_text("print('browser turn 24')", exact=False)).to_be_visible()
    session = page.evaluate(
        """async sessionId => {
            const response = await fetch(`/v1/sessions/${sessionId}`);
            return response.json();
        }""",
        chat.session_id,
    )
    assert session["harness"] == "claude"
    assert [model["id"] for model in session["model_options"]] == ["sonnet", "opus"]

    working = page.get_by_test_id("working-indicator")
    chat.emit_busy("fixture-turn")
    expect(working).to_be_visible(timeout=10_000)
    chat.emit_idle("fixture-turn")
    expect(working).to_be_hidden(timeout=10_000)


def test_contract_can_hold_and_release_session_skills(
    page: Page,
    chat_session_contract: ChatSessionContract,
) -> None:
    chat = chat_session_contract
    chat.set_skills([{"name": "review", "description": "Review the current change."}])
    release = chat.hold_skills()

    skills_url = f"{chat.base_url}/v1/skills?session_id={chat.session_id}"
    with page.expect_request(skills_url):
        page.goto(chat.url)

    for _ in range(100):
        if chat.skill_requests:
            break
        page.wait_for_timeout(10)
    assert chat.skill_requests == [
        {
            "url": f"{chat.base_url}/v1/skills?session_id={chat.session_id}",
            "method": "GET",
            "body": None,
        }
    ]
    with page.expect_response(skills_url) as response_info:
        release()

    assert response_info.value.json() == {
        "skills": [{"name": "review", "description": "Review the current change."}]
    }


def test_contract_records_and_persists_session_patches(
    page: Page,
    chat_session_contract: ChatSessionContract,
) -> None:
    chat = chat_session_contract
    page.goto(chat.url)

    sessions = page.evaluate(
        """async sessionId => {
            const response = await fetch(`/v1/sessions/${sessionId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ model_override: "opus", silent: true }),
            });
            const patched = await response.json();
            const current = await fetch(`/v1/sessions/${sessionId}`).then(result => result.json());
            return { patched, current };
        }""",
        chat.session_id,
    )

    assert chat.session_patches == [{"model_override": "opus", "silent": True}]
    assert sessions["patched"]["model_override"] == "opus"
    assert "silent" not in sessions["patched"]
    assert sessions["current"]["model_override"] == "opus"
