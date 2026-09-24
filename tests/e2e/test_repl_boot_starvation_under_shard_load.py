"""Real-time regression for concurrent REPL boots under CPU oversubscription.

Four local REPL/server/runner boots use a mock LLM and must reach a prompt
within 60 elapsed seconds of the test start.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.conftest import reset_mock_llm

pexpect = pytest.importorskip("pexpect")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ASK_DEMO_YAML = _REPO_ROOT / "tests" / "resources" / "agents" / "ask-demo" / "ask-demo.yaml"
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# Keep the original prompt-ready budget rather than hiding starvation.
_PROMPT_READY_BUDGET_S = 60.0

# Approximate co-located shards with concurrent boots and CPU contention.
_CONCURRENT_BOOTS = 4
_BURNERS_PER_CORE = 3

# Exit burners if their parent is killed.
_BURNER_SRC = "import os\np = os.getppid()\nwhile os.getppid() == p:\n    pass\n"


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences before substring search."""
    return _ANSI_RE.sub("", text)


def _build_repl_env(mock_llm_server_url: str, tmp_home: Path) -> dict[str, str]:
    """Use a mock LLM, isolated home, and this checkout for each REPL."""
    sdk_paths = [
        str(_REPO_ROOT),
        str(_REPO_ROOT / "sdks" / "python-client"),
        str(_REPO_ROOT / "sdks" / "ui"),
    ]
    existing_pp = os.environ.get("PYTHONPATH", "")
    merged_pp = (
        os.pathsep.join([*sdk_paths, existing_pp]) if existing_pp else os.pathsep.join(sdk_paths)
    )

    config_home = tmp_home / ".omnigent"
    config_home.mkdir(parents=True, exist_ok=True)
    (config_home / "config.yaml").write_text(
        "auto_open_conversation: false\ntui:\n  theme: dark\n",
    )

    real_databrickscfg = Path.home() / ".databrickscfg"
    env = {
        **os.environ,
        "OPENAI_API_KEY": "mock-key",
        "OPENAI_BASE_URL": f"{mock_llm_server_url}/v1",
        "HOME": str(tmp_home),
        "OMNIGENT_CONFIG_HOME": str(config_home),
        "DATABRICKS_CONFIG_FILE": str(real_databrickscfg),
        "OMNIGENT_SKIP_ONBOARD": "1",
        "OMNIGENT_NO_UPDATE_CHECK": "1",
        "PYTHONPATH": merged_pp,
        "TERM": "xterm-256color",
        "LINES": "40",
        "COLUMNS": "120",
        "PROMPT_TOOLKIT_NO_CPR": "1",
        # Keep mock and local server traffic off ambient proxies.
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    for k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE", "CLAUDECODE", "CODEX", "DATABRICKS_TOKEN"):
        env.pop(k, None)
    # Strip ambient runner identity so each REPL makes a fresh boot.
    runner_ambients = {
        "OMNIGENT",
        "OMNIGENT_USER_ID",
        "OMNIGENT_PROCESS_LOG_FILE",
        "RUNNER_SERVER_URL",
    }
    for k in [k for k in env if k.startswith("OMNIGENT_RUNNER") or k in runner_ambients]:
        env.pop(k, None)
    return env


def _spawn_repl(env: dict[str, str]) -> Any:
    """Spawn one ``omnigent run`` REPL under a PTY."""
    return pexpect.spawn(
        sys.executable,
        ["-m", "omnigent", "run", str(_ASK_DEMO_YAML), "--no-session"],
        env=env,
        cwd=str(_REPO_ROOT),
        encoding="utf-8",
        codec_errors="replace",
        timeout=_PROMPT_READY_BUDGET_S,
        dimensions=(40, 120),
    )


def test_repl_boot_reaches_prompt_ready_under_full_shard_load(
    mock_llm_server_url: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Every loaded REPL boot must show its prompt within 60 real seconds."""
    if os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1") != "1":
        pytest.skip(
            "pegs every core with CPU burners; run standalone (see module "
            "docstring), not inside a parallel xdist shard where it would "
            "starve co-located workers"
        )
    reset_mock_llm(mock_llm_server_url)

    ncpu = os.cpu_count() or 2
    # Clean up burners even if process creation fails midway.
    burners: list[subprocess.Popen[bytes]] = []
    children: list[Any] = []
    results: list[tuple[int, float, str, str]] = []
    t0 = time.monotonic()
    try:
        for _ in range(ncpu * _BURNERS_PER_CORE):
            burners.append(subprocess.Popen([sys.executable, "-c", _BURNER_SRC]))
        for i in range(_CONCURRENT_BOOTS):
            env = _build_repl_env(
                mock_llm_server_url,
                tmp_path_factory.mktemp(f"repl_boot_home_{i}"),
            )
            children.append(_spawn_repl(env))

        def _wait_prompt_ready(idx: int, child: Any) -> None:
            try:
                # Use the same input-ready marker as the REPL tests.
                child.expect("❯", timeout=_PROMPT_READY_BUDGET_S)
                dt = time.monotonic() - t0
                # Spawn stagger must not grant extra time.
                status = "ready" if dt <= _PROMPT_READY_BUDGET_S else "ready past budget"
                results.append((idx, dt, status, ""))
            except pexpect.TIMEOUT:
                tail = _strip_ansi(child.before or "")[-600:]
                results.append((idx, time.monotonic() - t0, "starved (60s TIMEOUT)", tail))
            except pexpect.EOF:
                tail = _strip_ansi(child.before or "")[-600:]
                results.append((idx, time.monotonic() - t0, "exited during boot (EOF)", tail))

        threads = [
            threading.Thread(target=_wait_prompt_ready, args=(i, c))
            for i, c in enumerate(children)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        # Unload first; let each REPL reap its server/runner before force-kill.
        for b in burners:
            b.kill()
        for child in children:
            with contextlib.suppress(Exception):
                child.delayafterterminate = 2.0
                child.terminate(force=True)
        for b in burners:
            with contextlib.suppress(Exception):
                b.wait(timeout=10)

    failures = [r for r in sorted(results) if r[2] != "ready"]
    summary = "\n".join(
        f"  repl[{idx}]: {status} at {dt:.1f}s"
        + (f"\n    tail: {tail.strip()[-300:]}" if tail else "")
        for idx, dt, status, tail in sorted(results)
    )
    assert not failures, (
        f"{len(failures)}/{_CONCURRENT_BOOTS} REPL boots starved past the "
        f"{_PROMPT_READY_BUDGET_S:.0f}s prompt-ready budget under full-shard load "
        f"(boot starvation counted as test failure).\n{summary}"
    )
