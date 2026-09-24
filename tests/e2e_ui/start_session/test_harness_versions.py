"""CLI subprocess → host tunnel → HTTP → actual browser picker."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

from omnigent.host.connect import HostProcess
from omnigent.host.identity import HostIdentity
from omnigent.onboarding import harness_install
from tests.e2e_ui.start_session.test_windows_native_picker_readiness import (
    _fake_host,
    _open_picker_on_host,
    _register_routes,
    _reveal_claude_row,
    _run_in_fresh_loop,
)


@pytest.mark.parametrize("width", [1100, 390], ids=["desktop", "mobile"])
def test_picker_versions_refresh_and_follow_host(
    live_server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\necho '2.1.0 (Claude Code)'\n")
    binary.chmod(0o755)
    monkeypatch.setattr(
        harness_install,
        "resolve_cli_binary",
        lambda name: str(binary) if name == "claude" else None,
    )
    _run_in_fresh_loop(_drive_picker(live_server, binary, width))


async def _drive_picker(base_url: str, binary: Path, width: int) -> None:
    host = HostProcess(
        identity=HostIdentity(host_id="versions-test", name="Version test host"),
        server_url=base_url,
    )
    async with (
        _fake_host(base_url, "Version test host", {"claude-native": True}, host=host) as host_id,
        _fake_host(base_url, "Older host", {"claude-native": True}) as old_host_id,
        async_playwright() as pw,
    ):
        browser = await pw.chromium.launch()
        context = await browser.new_context(viewport={"width": width, "height": 760})
        page = await context.new_page()
        try:
            await _register_routes(page)
            await _open_picker_on_host(page, base_url, host_id)
            row = await _reveal_claude_row(page)
            await expect(row.get_by_text("v2.1.0", exact=True)).to_be_visible()
            if evidence_dir := os.environ.get("OMNIGENT_TEST_EVIDENCE_DIR"):
                Path(evidence_dir).mkdir(parents=True, exist_ok=True)
                await page.screenshot(
                    path=str(Path(evidence_dir) / f"harness-versions-{width}.png")
                )
            binary.write_text("#!/bin/sh\necho '2.123.0 (Claude Code)'\n")
            # Exercise the real 30-second polling interval while the picker stays open.
            await expect(row.get_by_text("v2.123.0", exact=True)).to_be_visible(timeout=35_000)
            await page.keyboard.press("Escape")
            await expect(page.locator('[data-slot="dropdown-menu-content"]')).to_have_count(0)
            await _switch_host(page, old_host_id)
            row = await _reveal_claude_row(page)
            await expect(row.locator('[title^="Installed CLI version:"]')).to_have_count(0)
            await page.keyboard.press("Escape")
            await expect(page.locator('[data-slot="dropdown-menu-content"]')).to_have_count(0)
            await _switch_host(page, host_id)
            row = await _reveal_claude_row(page)
            await expect(row.get_by_text("v2.123.0", exact=True)).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(page.locator('[data-slot="dropdown-menu-content"]')).to_have_count(0)
            binary.unlink()
            await page.get_by_test_id("new-chat-landing-agent-select").click()
            row = await _reveal_claude_row(page)
            await expect(row.locator('[title^="Installed CLI version:"]')).to_have_count(0)
            # A missing version must never prevent choosing the harness.
            await row.click()
            await expect(page.get_by_test_id("new-chat-landing-agent-select")).to_have_attribute(
                "aria-label", re.compile("Claude Code")
            )
        finally:
            await context.close()
            await browser.close()


async def _switch_host(page, host_id: str) -> None:
    await page.get_by_test_id("new-chat-landing-host-chip").click()
    await page.get_by_test_id(f"new-chat-landing-host-{host_id}").click()
    await expect(page.locator('[data-slot="dropdown-menu-content"]')).to_have_count(0)
    await page.get_by_test_id("new-chat-landing-agent-select").click()
