"""Exercise every built-in CLI row and capture the picker for design review."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
import pytest
from playwright.async_api import Page, async_playwright, expect

from omnigent.harness_plugins import native_agents
from omnigent.host.connect import HostProcess
from omnigent.host.identity import HostIdentity
from omnigent.onboarding import harness_install
from tests.e2e_ui.start_session.test_windows_native_picker_readiness import (
    _fake_host,
    _register_routes,
    _run_in_fresh_loop,
)

# Deterministic display fixtures, including a date version and a prerelease.
# They exercise executable probes, not injected version-map responses.
_VERSIONS = {
    "claude": "2.1.280",
    "codex": "0.156.1",
    "cursor-agent": "2026.07.23",
    "pi": "0.85.1",
    "opencode": "1.17.9",
    "kiro-cli": "2.24.0",
    "goose": "1.41.0",
    "agy": "1.2.10",
    "qwen": "0.19.3",
    "kimi": "0.23.4",
    "hermes": "0.18.0",
    "devin": "0.1.0-beta.12",
}


@pytest.mark.parametrize("width", [1100, 390], ids=["desktop", "mobile"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_native_harness_version_in_picker(
    live_server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int, theme: str
) -> None:
    binaries = {}
    for name, version in _VERSIONS.items():
        binary = tmp_path / name
        binary.write_text(f"#!/bin/sh\nprintf '%s\\n' '{name} {version}'\n")
        binary.chmod(0o755)
        binaries[name] = str(binary)
    monkeypatch.setattr(harness_install, "resolve_cli_binary", binaries.get)
    agents = native_agents()
    expected = {}
    for agent in agents:
        spec = harness_install.required_cli_for_harness(agent.harness)
        assert spec is not None
        expected[agent.harness] = _VERSIONS[spec.binary]
    _run_in_fresh_loop(_drive_catalog(live_server, width, theme, expected, binaries))


async def _drive_catalog(
    base_url: str, width: int, theme: str, versions: dict[str, str], binaries: dict[str, str]
) -> None:
    agents = native_agents()
    catalog = [
        {"id": f"version-{agent.key}", "name": agent.agent_name, "harness": agent.harness}
        for agent in agents
    ] + [
        {"id": "version-claude-sdk", "name": "claude-sdk", "harness": "claude-sdk"},
        {"id": "version-openai-sdk", "name": "openai-agents", "harness": "openai-agents"},
    ]
    readiness = dict.fromkeys(versions, True)
    readiness.update({"claude-sdk": True, "openai-agents": True})
    host = HostProcess(
        identity=HostIdentity(host_id="catalog-versions", name="Harness catalog"),
        server_url=base_url,
    )
    async with (
        _fake_host(base_url, f"Catalog {width} {theme}", readiness, host=host) as host_id,
        async_playwright() as pw,
    ):
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": width, "height": 900})
        prefix = f"catalog-{width}-{theme}"
        try:
            await _register_routes(page)
            await page.route("**/v1/agents", lambda route: route.fulfill(json={"data": catalog}))
            await page.route(
                re.compile(r"/model-options(?:\?.*)?$"),
                lambda route: route.fulfill(
                    json={
                        "models": [
                            {
                                "id": "preview",
                                "model": "fixture-model",
                                "displayName": "Preview model",
                                "isDefault": True,
                            }
                        ]
                    }
                ),
            )
            await page.add_init_script(
                f"localStorage.setItem('web-theme', {json.dumps(theme)});"
                "localStorage.setItem('omnigent:recent-workspaces', "
                + json.dumps(json.dumps({host_id: ["/work/project"]}))
                + ");"
            )
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{base_url}/v1/hosts/{host_id}/harness-versions")
                assert response.status_code == 200
                assert {key: response.json()["versions"].get(key) for key in versions} == versions
            await page.goto(base_url)
            await page.get_by_test_id("new-chat-landing-input").wait_for(state="visible")
            await page.get_by_test_id("new-chat-landing-host-chip").click()
            await page.get_by_test_id(f"new-chat-landing-host-{host_id}").click()
            await expect(page.locator('[data-slot="dropdown-menu-content"]')).to_have_count(0)
            await page.get_by_test_id("new-chat-landing-agent-select").click()
            await expect(
                page.get_by_test_id("new-chat-landing-agent-version-codex").get_by_text(
                    "v0.156.1", exact=True
                )
            ).to_be_visible()
            await _capture(page, f"{prefix}-primary")
            await page.get_by_test_id("new-chat-landing-harness-more").click()
            await expect(
                page.get_by_test_id("new-chat-landing-agent-version-devin")
            ).to_be_visible()
            await _capture(page, f"{prefix}-other")
            await _close_picker(page)

            for agent in agents:
                await page.get_by_test_id("new-chat-landing-agent-select").click()
                row = page.get_by_test_id(f"new-chat-landing-agent-version-{agent.key}")
                if not await row.count():
                    await page.get_by_test_id("new-chat-landing-harness-more").click()
                await expect(
                    row.get_by_text(f"v{versions[agent.harness]}", exact=True)
                ).to_be_visible()
                await row.locator(".composer-agent-choice").click()
                await expect(page.locator('[data-slot="dropdown-menu-content"]')).to_have_count(0)
                await page.get_by_test_id("new-chat-landing-agent-select").click()
                await expect(
                    row.get_by_text(f"v{versions[agent.harness]}", exact=True)
                ).to_be_visible()
                await _capture(page, f"{prefix}-{agent.key}")
                await _close_picker(page)

            # An installed CLI must not supply a version to an SDK-only agent.
            await page.get_by_test_id("new-chat-landing-agent-select").click()
            await page.get_by_test_id("new-chat-landing-custom-agents").click()
            for agent_id in ("claude-sdk", "openai-sdk"):
                row = page.get_by_test_id(f"new-chat-landing-agent-version-{agent_id}")
                await expect(row).to_be_visible()
                await expect(row.locator('[title^="Installed CLI version:"]')).to_have_count(0)
            await _capture(page, f"{prefix}-sdk")
            await _close_picker(page)

            # Clear only one vendor's version: its installed CLI is now unparseable.
            Path(binaries["cursor-agent"]).write_text("#!/bin/sh\necho 'version unavailable'\n")
            await page.get_by_test_id("new-chat-landing-agent-select").click()
            cursor = page.get_by_test_id("new-chat-landing-agent-version-cursor")
            await expect(cursor.locator('[title^="Installed CLI version:"]')).to_have_count(0)
            await expect(
                page.get_by_test_id("new-chat-landing-agent-version-codex").get_by_text(
                    "v0.156.1", exact=True
                )
            ).to_be_visible()
            await _capture(page, f"{prefix}-unknown-version")
            await _close_picker(page)

            # An installed but unauthenticated CLI still has a version; a missing one does not.
            Path(binaries["cursor-agent"]).write_text("#!/bin/sh\necho '2026.07.23'\n")
            Path(binaries["devin"]).unlink()
            unavailable = {
                **readiness,
                "cursor-native": "needs-auth",
                "devin-native": "binary-missing",
            }
            async with _fake_host(
                base_url, f"Needs setup {width} {theme}", unavailable, host=host
            ) as setup_host:
                await page.get_by_test_id("new-chat-landing-agent-select").click()
                await (
                    page.get_by_test_id("new-chat-landing-agent-version-claude")
                    .locator(".composer-agent-choice")
                    .click()
                )
                await expect(page.locator('[data-slot="dropdown-menu-content"]')).to_have_count(0)
                await page.get_by_test_id("new-chat-landing-host-chip").click()
                await page.get_by_test_id(f"new-chat-landing-host-{setup_host}").click()
                await expect(page.locator('[data-slot="dropdown-menu-content"]')).to_have_count(0)
                await page.get_by_test_id("new-chat-landing-agent-select").click()
                await page.get_by_test_id("new-chat-landing-harness-more").click()
                cursor = page.get_by_test_id("new-chat-landing-agent-version-cursor")
                devin = page.get_by_test_id("new-chat-landing-agent-version-devin")
                await expect(cursor.get_by_text("v2026.07.23", exact=True)).to_be_visible()
                await expect(cursor).to_have_attribute("aria-disabled", "true")
                await expect(devin).to_have_attribute("aria-disabled", "true")
                await expect(devin.locator('[title^="Installed CLI version:"]')).to_have_count(0)
                await _capture(page, f"{prefix}-needs-setup")
        finally:
            await page.unroute_all(behavior="wait")
            await browser.close()


async def _close_picker(page: Page) -> None:
    # Desktop submenus consume the first Escape before closing the parent.
    await page.keyboard.press("Escape")
    if await page.locator('[data-slot="dropdown-menu-content"]').count():
        await page.keyboard.press("Escape")
    await expect(page.locator('[data-slot="dropdown-menu-content"]')).to_have_count(0)


async def _capture(page: Page, name: str) -> None:
    if directory := os.environ.get("OMNIGENT_TEST_EVIDENCE_DIR"):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=path / f"{name}.png", animations="disabled")
        boxes = [
            box
            for menu in await page.locator(".composer-agent-menu").all()
            if await menu.is_visible() and (box := await menu.bounding_box()) is not None
        ]
        (path / f"{name}.json").write_text(json.dumps({"menus": boxes}))
