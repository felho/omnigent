"""Browser regression for rendered timestamps in an imported session."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect

_SOURCE_RECORDS: tuple[tuple[str, str, str], ...] = (
    ("user", "2026-07-21T12:00:00.000Z", "inspect TODO.md"),
    ("assistant", "2026-07-21T12:00:30.000Z", "TODO.md has three items."),
    ("user", "2026-07-21T12:09:00.000Z", "fix the first one"),
    ("assistant", "2026-07-21T12:10:00.000Z", "Done."),
)


def _seed_claude_transcript(home: Path, session_id: str) -> None:
    """Write the July transcript and set its mtime to the last activity time."""
    transcript = home / ".claude" / "projects" / "-repo" / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    parent: str | None = None
    for index, (record_type, iso_timestamp, text) in enumerate(_SOURCE_RECORDS):
        uuid = f"{record_type}-{index}"
        if record_type == "user":
            message: dict[str, object] = {"role": "user", "content": text}
        else:
            message = {"role": "assistant", "content": [{"type": "text", "text": text}]}
        lines.append(
            json.dumps(
                {
                    "type": record_type,
                    "uuid": uuid,
                    "parentUuid": parent,
                    "sessionId": session_id,
                    "timestamp": iso_timestamp,
                    "cwd": "/repo",
                    "message": message,
                }
            )
        )
        parent = uuid
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.utime(transcript, (1784635800, 1784635800))


@pytest.mark.flaky(reruns=2, reruns_delay=5)
def test_imported_message_timestamps_reflect_source_not_import(
    page: Page,
    live_server: str,
    tmp_path: Path,
) -> None:
    """An imported message displays its July source time."""
    source_session_id = str(uuid4())
    _seed_claude_transcript(tmp_path, source_session_id)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path),
            "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude"),
            "OMNIGENT_CONFIG_HOME": str(tmp_path / "config"),
            "OMNIGENT_DATA_DIR": str(tmp_path / "omnigent-data"),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "omnigent",
            "import",
            "--harness",
            "claude",
            "--session",
            source_session_id,
            "--server",
            live_server,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    match = re.search(r"Imported \d+ item\(s\).*?/c/(\S+)", result.stdout)
    assert match is not None, result.stdout
    session_id = match.group(1)

    # Imported sessions keep SSE open, so wait on transcript content.
    page.goto(f"{live_server}/c/{session_id}")
    # The message text also appears in the sidebar and header.
    transcript = page.get_by_role("log")
    first_message = transcript.get_by_text("inspect TODO.md")
    expect(first_message).to_be_visible(timeout=30_000)

    first_message.hover()
    timestamps = transcript.get_by_test_id("message-timestamp")
    expect(timestamps.first).to_be_visible(timeout=10_000)
    # Hold the hover-revealed timestamp for the recording.
    page.wait_for_timeout(1500)

    stamp_texts = timestamps.all_inner_texts()
    assert any("Jul" in text for text in stamp_texts), (
        "no imported message shows its July source date; every message time "
        f"reflects the import run instead: {stamp_texts}"
    )
