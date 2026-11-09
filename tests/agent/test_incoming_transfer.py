# on_incoming 에서 call.transfer(to) — AI 세션 없이 받아 곧바로 넘긴다.
from __future__ import annotations

import asyncio
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest

import clawops.agent._agent as agent_mod
from clawops.agent import ClawOpsAgent, IncomingCall


class _ControlWS:
    """control WS 대역 — 보낸 프레임을 모으고, 전환 요청에 정해 둔 결과를 차례로 돌려준다."""

    def __init__(self, results: list | None = None) -> None:
        self.sent: list[dict] = []
        self.transfers: list[tuple[str, dict]] = []
        self._results = list(results or [{"status": "completed"}])

    async def send(self, msg: dict) -> None:
        self.sent.append(msg)

    async def request_transfer(self, call_id: str, params: dict) -> dict:
        self.transfers.append((call_id, params))
        r = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        if isinstance(r, BaseException):
            raise r
        if r == "hang":  # 결과가 오지 않는다(대상이 울리는 중)
            await asyncio.Event().wait()
        return r


class _FakeMedia:
    """MediaWebSocket 대역. connect() 는 첫 음성 프레임을 넣고 close() 될 때까지 머문다."""

    instances: ClassVar[list[_FakeMedia]] = []
    send_audio_frame = True

    def __init__(self, *, url: str, on_audio, on_start, on_stop, **_: Any) -> None:
        self.url = url
        self._on_audio = on_audio
        self._closed = asyncio.Event()
        self.closed_by_sdk = False
        _FakeMedia.instances.append(self)

    async def connect(self) -> None:
        if _FakeMedia.send_audio_frame:
            await asyncio.sleep(0.01)
            await self._on_audio(b"\xff" * 160, 0)
        await self._closed.wait()

    def server_close(self) -> None:
        self._closed.set()

    async def close(self) -> None:
        self.closed_by_sdk = True
        self._closed.set()


@pytest.fixture(autouse=True)
def _fake_media(monkeypatch):
    _FakeMedia.instances = []
    _FakeMedia.send_audio_frame = True
    monkeypatch.setattr(agent_mod, "MediaWebSocket", _FakeMedia)
    monkeypatch.setattr(agent_mod, "TRANSFER_ONLY_RETRY_INTERVAL_S", 0.01)


def _agent(results: list | None = None, *, factory: bool = True) -> tuple[ClawOpsAgent, _ControlWS, MagicMock]:
    session = MagicMock()
    session.session_setup = AsyncMock()
    built: list[MagicMock] = []

    def make():
        built.append(session)
        return session

    kwargs = {"session_factory": make} if factory else {"session": session}
    agent = ClawOpsAgent(api_key="sk_test", account_id="AC_test", from_="07012341234", **kwargs)
    agent._safe_start_call_session = AsyncMock()  # type: ignore[method-assign]
    ws = _ControlWS(results)
    agent._control_ws = ws  # type: ignore[assignment]
    agent._incoming_hook_deadline_s = 0.2
    session.built = built
    return agent, ws, session


def _incoming(call_id: str = "C1", **extra) -> dict:
    return {"event": "call.incoming", "callId": call_id, "from": "01062915351", "mediaUrl": "wss://m", **extra}


async def _until(pred, timeout: float = 2.0) -> None:
    for _ in range(int(timeout / 0.01)):
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


@pytest.mark.asyncio
async def test_transfer_accepts_without_ai_session_and_transfers_after_audio():
    agent, ws, session = _agent()

    @agent.on_incoming
    async def screen(call: IncomingCall):
        await call.transfer("0221012266", caller_id_mode="original", failure_message="연결이 어렵습니다")

    await agent._handle_incoming(_incoming())
    await _until(lambda: ws.transfers)

    assert ws.sent == [{"event": "call.accept", "callId": "C1"}]
    call_id, params = ws.transfers[0]
    assert call_id == "C1"
    assert params["to"] == "0221012266"
    assert params["mode"] == "blind"
    assert params["afterTransfer"] == "terminate"
    assert params["callerIdMode"] == "original"
    assert params["failureMessage"] == "연결이 어렵습니다"
    # AI 세션은 만들지도 시작하지도 않는다.
    assert session.built == []
    agent._safe_start_call_session.assert_not_called()  # type: ignore[attr-defined]
    assert "C1" not in agent._call_sessions

    _FakeMedia.instances[0].server_close()  # 전환이 끝나 서버가 미디어를 닫았다
    await _until(lambda: "C1" not in agent._active_sessions)
    assert _FakeMedia.instances[0].closed_by_sdk is False


@pytest.mark.asyncio
async def test_no_session_rejection_is_retried():
    no_session = {"status": "failed", "reason": "No active media session"}
    agent, ws, _ = _agent([no_session, no_session, {"status": "completed"}])

    @agent.on_incoming
    def screen(call: IncomingCall):
        call.transfer("0221012266")

    await agent._handle_incoming(_incoming())
    await _until(lambda: len(ws.transfers) == 3)
    await asyncio.sleep(0.05)
    assert len(ws.transfers) == 3
    assert _FakeMedia.instances[0].closed_by_sdk is False
    _FakeMedia.instances[0].server_close()
    await _until(lambda: "C1" not in agent._active_sessions)


@pytest.mark.asyncio
async def test_rejected_before_start_hangs_up_instead_of_silence():
    agent, ws, _ = _agent([{"status": "failed", "reason": "Invalid phone number"}])

    @agent.on_incoming
    async def screen(call: IncomingCall):
        await call.transfer("123")

    await agent._handle_incoming(_incoming())
    await _until(lambda: ws.transfers)
    await _until(lambda: "C1" not in agent._active_sessions)
    assert len(ws.transfers) == 1
    assert _FakeMedia.instances[0].closed_by_sdk is True


@pytest.mark.asyncio
async def test_target_not_answering_is_left_to_server():
    # 시작은 했지만 대상이 받지 않았다 — 서버가 안내 후 끊는다. SDK 가 먼저 끊지 않는다.
    agent, ws, _ = _agent([{"status": "failed"}])

    @agent.on_incoming
    async def screen(call: IncomingCall):
        await call.transfer("0221012266")

    await agent._handle_incoming(_incoming())
    await _until(lambda: ws.transfers)
    await asyncio.sleep(0.05)
    assert _FakeMedia.instances[0].closed_by_sdk is False
    _FakeMedia.instances[0].server_close()
    await _until(lambda: "C1" not in agent._active_sessions)


@pytest.mark.asyncio
async def test_caller_hangs_up_before_audio_skips_transfer():
    _FakeMedia.send_audio_frame = False
    agent, ws, _ = _agent()

    @agent.on_incoming
    async def screen(call: IncomingCall):
        await call.transfer("0221012266")

    await agent._handle_incoming(_incoming())
    await _until(lambda: _FakeMedia.instances)
    _FakeMedia.instances[0].server_close()
    await _until(lambda: "C1" not in agent._active_sessions)
    assert ws.transfers == []


@pytest.mark.asyncio
async def test_other_callers_still_get_ai():
    agent, ws, _ = _agent()

    @agent.on_incoming
    async def screen(call: IncomingCall):
        if call.from_number != "01062915351":
            await call.transfer("0221012266")

    await agent._handle_incoming(_incoming())
    await _until(lambda: ws.sent)
    await asyncio.sleep(0.05)
    assert ws.transfers == []
    agent._safe_start_call_session.assert_called_once()  # type: ignore[attr-defined]


def test_first_decision_wins():
    a = IncomingCall(call_id="C", from_number="010", to_number="070")
    a.transfer("0221012266")
    a.reject()
    assert a.transfer_request is not None and a.reject_reason is None

    b = IncomingCall(call_id="C", from_number="010", to_number="070")
    b.reject("busy")
    b.transfer("0221012266")
    assert b.transfer_request is None and b.reject_reason == "busy"


def test_bad_caller_id_mode_raises():
    c = IncomingCall(call_id="C", from_number="010", to_number="070")
    with pytest.raises(ValueError):
        c.transfer("0221012266", caller_id_mode="orignal")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_lost_result_after_send_waits_for_server_instead_of_hanging_up():
    # drain·재연결로 대기가 취소됐다 — 요청은 나갔으므로 서버 쪽 전환은 계속된다. 끊지 않는다.
    agent, ws, _ = _agent([asyncio.CancelledError()])

    @agent.on_incoming
    async def screen(call: IncomingCall):
        await call.transfer("0221012266")

    await agent._handle_incoming(_incoming())
    await _until(lambda: ws.transfers)
    await asyncio.sleep(0.05)
    assert _FakeMedia.instances[0].closed_by_sdk is False
    assert "C1" in agent._active_sessions  # drain 이 기다리도록 남아 있다
    _FakeMedia.instances[0].server_close()
    await _until(lambda: "C1" not in agent._active_sessions)
    assert _FakeMedia.instances[0].closed_by_sdk is False


@pytest.mark.asyncio
async def test_request_not_sent_hangs_up():
    from clawops._exceptions import AgentConnectionError

    agent, ws, _ = _agent([AgentConnectionError("transfer 불가")])

    @agent.on_incoming
    async def screen(call: IncomingCall):
        await call.transfer("0221012266")

    await agent._handle_incoming(_incoming())
    await _until(lambda: ws.transfers)
    await _until(lambda: "C1" not in agent._active_sessions)
    assert _FakeMedia.instances[0].closed_by_sdk is True


@pytest.mark.asyncio
async def test_media_closed_while_waiting_result_ends_cleanly():
    # 전환이 시작되면 서버가 미디어를 닫는다. 결과는 대상 통화가 끝나야 오므로 기다리지 않는다.
    agent, ws, _ = _agent(["hang"])

    @agent.on_incoming
    async def screen(call: IncomingCall):
        await call.transfer("0221012266")

    await agent._handle_incoming(_incoming())
    await _until(lambda: ws.transfers)
    _FakeMedia.instances[0].server_close()
    await _until(lambda: "C1" not in agent._active_sessions)
    assert _FakeMedia.instances[0].closed_by_sdk is False
    assert not agent._transfer_only_tasks
