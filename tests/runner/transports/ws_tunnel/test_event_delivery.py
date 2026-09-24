"""Native event batches retain their source cursor until a tunnel acknowledgement."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from omnigent.runner.transports.ws_tunnel.event_delivery import (
    RunnerEventDispatcher,
    TunnelEventClient,
)
from omnigent.runner.transports.ws_tunnel.frames import (
    EventAckFrame,
    EventBatchFrame,
    EventReadyFrame,
    decode_frame,
    encode_frame,
)
from omnigent.runner.transports.ws_tunnel.serve import _handle_tunnel_frame

_URL = "/v1/sessions/session-a/events"
_ITEM = {
    "type": "external_conversation_item",
    "data": {
        "source_id": "record-1",
        "item_type": "message",
        "item_data": {"role": "assistant", "content": []},
    },
}


def _client(dispatcher: RunnerEventDispatcher, http_posts: list[object]) -> TunnelEventClient:
    def handler(request: httpx.Request) -> httpx.Response:
        http_posts.append(json.loads(request.content))
        return httpx.Response(202, json={"queued": False})

    return TunnelEventClient(
        event_dispatcher=dispatcher,
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    )


async def test_runner_handler_routes_ready_and_ack_to_delivery_queue() -> None:
    dispatcher = RunnerEventDispatcher()
    http_posts: list[object] = []
    sent: list[EventBatchFrame] = []

    async def noop_app(*_args: Any) -> None:
        pass

    async def send(text: str) -> None:
        frame = decode_frame(text)
        assert isinstance(frame, EventBatchFrame)
        sent.append(frame)
        await _handle_tunnel_frame(
            noop_app,
            encode_frame(EventAckFrame(frame.id, 1)),
            send,
            {},
            {},
            event_dispatcher=dispatcher,
        )

    await _handle_tunnel_frame(
        noop_app,
        encode_frame(EventReadyFrame()),
        send,
        {},
        {},
        event_dispatcher=dispatcher,
    )
    async with _client(dispatcher, http_posts) as client:
        response = await client.post(_URL, json=_ITEM)
    assert response.status_code == 202
    assert len(sent) == 1 and http_posts == []


async def test_acknowledged_item_uses_tunnel_not_http() -> None:
    dispatcher = RunnerEventDispatcher()
    http_posts: list[object] = []
    frames: list[EventBatchFrame] = []

    async def send(text: str) -> None:
        frame = decode_frame(text)
        assert isinstance(frame, EventBatchFrame)
        frames.append(frame)
        dispatcher.acknowledge(EventAckFrame(frame.id, len(frame.events)))

    dispatcher.ready(send)
    async with _client(dispatcher, http_posts) as client:
        response = await client.post(_URL, json=_ITEM)
    assert response.status_code == 202
    assert frames[0].session_id == "session-a"
    assert frames[0].events == [_ITEM]
    assert http_posts == []


async def test_lost_ack_replays_after_tunnel_reconnect() -> None:
    dispatcher = RunnerEventDispatcher()
    http_posts: list[object] = []
    frames: list[EventBatchFrame] = []

    async def first_send(text: str) -> None:
        assert dispatcher.has_pending
        frame = decode_frame(text)
        assert isinstance(frame, EventBatchFrame)
        frames.append(frame)
        # The server may already have applied it, but its ACK was lost.
        dispatcher.disconnected()
        asyncio.get_running_loop().call_soon(dispatcher.ready, second_send)

    async def second_send(text: str) -> None:
        frame = decode_frame(text)
        assert isinstance(frame, EventBatchFrame)
        frames.append(frame)
        dispatcher.acknowledge(EventAckFrame(frame.id, 1))

    dispatcher.ready(first_send)
    async with _client(dispatcher, http_posts) as client:
        response = await asyncio.wait_for(client.post(_URL, json=_ITEM), timeout=2)
    assert response.status_code == 202
    assert [frame.events[0]["data"]["source_id"] for frame in frames] == ["record-1", "record-1"]
    assert http_posts == []
    assert not dispatcher.has_pending


async def test_old_server_and_unacknowledged_child_batch_use_http() -> None:
    dispatcher = RunnerEventDispatcher()
    http_posts: list[object] = []
    async with _client(dispatcher, http_posts) as client:
        response = await client.post(_URL, json=_ITEM)
        child = await client.post(_URL, json=[_ITEM, _ITEM])
    assert response.status_code == child.status_code == 202
    assert http_posts == [_ITEM, [_ITEM, _ITEM]]


async def test_preview_drops_when_negotiated_tunnel_is_down() -> None:
    dispatcher = RunnerEventDispatcher()
    http_posts: list[object] = []

    async def send(_text: str) -> None:
        raise AssertionError("disconnected tunnel must not send")

    dispatcher.ready(send)
    dispatcher.disconnected()
    preview = {"type": "external_output_text_delta", "data": {"delta": "hi"}}
    async with _client(dispatcher, http_posts) as client:
        response = await asyncio.wait_for(client.post(_URL, json=preview), timeout=3)
    assert response.status_code == 503
    assert http_posts == []
