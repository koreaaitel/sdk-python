# tests/agent/test_transfer.py
"""Tests for transfer event handling in ControlWebSocket."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from clawops.agent._control_ws import ControlWebSocket


def _make_control_ws(**overrides) -> ControlWebSocket:
    defaults = dict(
        base_url="http://localhost:3000",
        api_key="test-key",
        account_id="AC123",
        number="07012341234",
        on_call_incoming=AsyncMock(),
        on_call_ended=AsyncMock(),
    )
    defaults.update(overrides)
    return ControlWebSocket(**defaults)


def _arm(cws: ControlWebSocket, call_id: str, request_id: str = "REQ-1"):
    """대기를 직접 심는다.

    대기는 **요청 단위**로 키잉된다(callId 아님) — 같은 통화에서 전환을 다시 타는
    return 모드가 이전 대기를 덮어쓰지 않게. 자세한 규약은 test_transfer_correlation.py.
    """
    future = asyncio.get_event_loop().create_future()
    cws._pending_transfers[request_id] = future
    cws._pending_by_call.setdefault(call_id, set()).add(request_id)
    return future, request_id


@pytest.mark.asyncio
async def test_request_transfer_sends_message_and_waits():
    """request_transfer sends the correct JSON and resolves on completed."""
    cws = _make_control_ws()
    mock_ws = MagicMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()
    cws._ws = mock_ws

    call_id = "CALL-001"
    transfer_params = {"destination": "07099998888", "timeout": 20}

    # Start request_transfer in background
    task = asyncio.create_task(
        cws.request_transfer(call_id, transfer_params)
    )
    # Let the coroutine progress to the await point
    await asyncio.sleep(0)

    # Verify the message was sent
    mock_ws.send_str.assert_called_once()
    sent = json.loads(mock_ws.send_str.call_args[0][0])
    assert sent["event"] == "call.transfer"
    assert sent["callId"] == call_id
    # 호출자가 준 파라미터는 그대로 실리고, 상관 ID 가 덧붙는다(서버가 결과에 echo).
    assert {k: v for k, v in sent["transfer"].items() if k != "requestId"} == transfer_params
    request_id = sent["transfer"]["requestId"]
    assert request_id

    # Simulate completed event
    cws._on_transfer_event({
        "event": "call.transfer.completed",
        "callId": call_id,
        "requestId": request_id,
        "transfer": {"status": "completed"},
    })

    result = await task
    assert result == {"status": "completed"}
    assert cws._pending_transfers == {}


@pytest.mark.asyncio
async def test_on_transfer_event_completed_resolves_future():
    """_on_transfer_event with completed resolves the pending future."""
    cws = _make_control_ws()
    call_id = "CALL-002"
    future, request_id = _arm(cws, call_id)

    cws._on_transfer_event({
        "event": "call.transfer.completed",
        "callId": call_id,
        "transfer": {"status": "completed", "duration": 5},
    })

    assert future.done()
    assert await future == {"status": "completed", "duration": 5}
    assert cws._pending_transfers == {}


@pytest.mark.asyncio
async def test_on_transfer_event_failed_resolves_future():
    """_on_transfer_event with failed resolves the pending future."""
    cws = _make_control_ws()
    call_id = "CALL-003"
    future, request_id = _arm(cws, call_id)

    cws._on_transfer_event({
        "event": "call.transfer.failed",
        "callId": call_id,
        "transfer": {"status": "failed", "reason": "no-answer"},
    })

    assert future.done()
    assert await future == {"status": "failed", "reason": "no-answer"}
    assert cws._pending_transfers == {}


@pytest.mark.asyncio
async def test_on_transfer_event_ignores_unknown_call_id():
    """_on_transfer_event ignores events for unknown callIds."""
    cws = _make_control_ws()
    call_id = "CALL-KNOWN"
    future, request_id = _arm(cws, call_id)

    # Event with unknown callId should be ignored
    cws._on_transfer_event({
        "event": "call.transfer.completed",
        "callId": "CALL-UNKNOWN",
        "transfer": {"status": "completed"},
    })

    assert not future.done()
    assert request_id in cws._pending_transfers


@pytest.mark.asyncio
async def test_on_transfer_event_ignores_started_event():
    """_on_transfer_event does not resolve future on started/connected events."""
    cws = _make_control_ws()
    call_id = "CALL-004"
    future, request_id = _arm(cws, call_id)

    cws._on_transfer_event({
        "event": "call.transfer.started",
        "callId": call_id,
        "transfer": {"status": "started"},
    })

    assert not future.done()
    assert request_id in cws._pending_transfers


@pytest.mark.asyncio
async def test_close_cancels_pending_transfer_futures():
    """close() cancels all pending transfer futures."""
    cws = _make_control_ws()
    future, _ = _arm(cws, "CALL-005")

    await cws.close()

    assert future.cancelled()
    assert len(cws._pending_transfers) == 0
