"""내장 transfer_call 도구가 모델에게 돌려주는 값 — 파이프라인·realtime 공통 경로."""
from __future__ import annotations

import asyncio
import json

import pytest

from clawops.agent._session import CallSession
from clawops.agent._transfer_tool import transfer_result_for_model, wait_for_transfer_result
from clawops.agent.pipeline._builtin_tool_schemas import execute_builtin_tool


def _call(transfer_fn) -> CallSession:
    call = CallSession(call_id="c", from_number="010", to_number="070", account_id="AC")
    call._transfer_fn = transfer_fn
    return call


@pytest.mark.parametrize(
    "status,expected",
    [
        ("no-answer", "did not answer"),
        ("busy", "busy"),
        ("failed", "could not be connected"),
        ("completed", "finished the call"),
        ("canceled", "hung up"),
    ],
)
def test_result_message(status: str, expected: str) -> None:
    body = json.loads(transfer_result_for_model({"status": status}))
    assert body["status"] == status
    assert expected in body["message"]


def test_result_without_status_is_a_failure() -> None:
    assert json.loads(transfer_result_for_model({}))["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("after_transfer", ["terminate", "return"])
async def test_native_sessions_do_not_wait(after_transfer: str) -> None:
    """자체 세션은 기다리면 대화 기록이 깨지거나 수신 루프가 막힌다(모듈 헤더)."""
    pending: asyncio.Future = asyncio.get_running_loop().create_future()

    async def transfer(_payload: dict) -> dict:
        return await pending

    result = await execute_builtin_tool(
        "transfer_call", {"to": "010", "after_transfer": after_transfer}, _call(transfer)
    )
    assert json.loads(result)["status"] == "transfer_initiated"
    pending.cancel()


def _never_ended() -> bool:
    return False


@pytest.mark.asyncio
async def test_cancelled_tool_keeps_the_transfer_request() -> None:
    pending: asyncio.Future = asyncio.get_running_loop().create_future()
    transfer = asyncio.ensure_future(pending)
    tool = asyncio.create_task(wait_for_transfer_result(transfer, call_ended=_never_ended))
    await asyncio.sleep(0)
    tool.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tool
    assert not transfer.cancelled()
    pending.set_result({"status": "busy"})
    assert (await transfer)["status"] == "busy"


@pytest.mark.asyncio
async def test_call_ending_without_a_result_is_reported() -> None:
    pending: asyncio.Future = asyncio.get_running_loop().create_future()
    tool = asyncio.create_task(wait_for_transfer_result(pending, call_ended=lambda: True))
    await asyncio.sleep(0)
    pending.cancel()
    assert json.loads(await tool)["status"] == "canceled"


@pytest.mark.asyncio
async def test_drain_is_not_reported_as_hang_up() -> None:
    """drain 은 통화를 살려 둔 채 전환 대기를 취소한다."""
    pending: asyncio.Future = asyncio.get_running_loop().create_future()
    tool = asyncio.create_task(wait_for_transfer_result(pending, call_ended=_never_ended))
    await asyncio.sleep(0)
    pending.cancel()
    assert json.loads(await tool)["status"] == "unknown"


@pytest.mark.asyncio
async def test_connection_loss_releases_the_wait() -> None:
    """유실된 결과를 끝까지 기다리면 끼어들기가 막힌 채 통화가 멈춘다."""
    loop = asyncio.get_running_loop()
    pending: asyncio.Future = loop.create_future()
    lost: asyncio.Future = loop.create_future()
    tool = asyncio.create_task(
        wait_for_transfer_result(pending, call_ended=_never_ended, connection_lost=lost)
    )
    await asyncio.sleep(0)
    lost.set_result(None)
    assert json.loads(await tool)["status"] == "unknown"
    assert not pending.done()
    pending.cancel()
