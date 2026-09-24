# Browser chat contract

`chat_session_contract` renders the real built SPA at `handle.url` without an
Omnigent server, runner, database, or model. It sits on the strict
`browser_contract` guard, so any undeclared API, SSE, or WebSocket dependency
fails the test.

```python
def test_chat(page, chat_session_contract):
    chat = chat_session_contract
    chat.seed_transcript(40)  # 40 user/assistant turns with Markdown + code
    chat.set_catalog(
        harness="claude",
        models=[model_option("sonnet", is_default=True)],
        selected_model="sonnet",
    )
    page.goto(chat.url)
    chat.emit_busy("turn-1")
    chat.emit_idle("turn-1")
```

The mutable handle exposes `session_id`, `url`, `event_posts`,
`upload_requests`, `skills`, and `skill_requests`. Use `set_skills(...)` to
replace the session's `/v1/skills` response. To exercise loading UI, call
`release = hold_skills()` before navigation, then call `release()` after the
request appears in `skill_requests`.

Event POSTs default to a queued acknowledgement, keeping the local turn busy so
another composer submission enters the client queue. Set `event_ack` to change
that response. Uploads are recorded and rejected by default; set
`reject_uploads = False` when a test intentionally exercises the successful
upload path.

Use the builders in `session_contract.py` for canonical list, transcript,
model-option, and `session.status` payloads. `emit()` accepts their named SSE
wire shape and can drive arbitrary scripted stream events after navigation.
