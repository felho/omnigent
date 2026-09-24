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
