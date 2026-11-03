"""재연결 판정 — 롤링 배포가 무중단이 되느냐 장애가 되느냐가 여기서 갈린다.

control 연결은 번호당 하나뿐이다. 서버가 그 자리를 새 프로세스에 넘겼는데 교체된 쪽이
재연결하면, 자기 연결을 되찾는 게 아니라 **방금 인계받은 프로세스를 밀어낸다**. 그러면
그쪽이 다시 재연결해 이쪽을 밀어내고, 겹치는 창 내내 자리를 주고받다가 서버의 reconnect
throttle 이 번호를 분 단위로 격리한다. 그래서 "재연결했는가" 는 실제 서버가 실제 소켓을
닫는 것에 대고 고정해야 한다.

이 파일은 이 스위트에서 처음으로 진짜 WebSocket 서버를 띄운다 — 그전까지는 URL 조립만
검증했고, 그래서 재연결 동작이 검증된 적이 없었다.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web

from clawops._exceptions import AgentError
from clawops.agent._control_ws import (
    CLOSE_OWNERSHIP_LOST,
    CLOSE_REPLACED,
    ControlWebSocket,
)


class _Server:
    def __init__(self) -> None:
        self.accepted: list[web.WebSocketResponse] = []
        self.received: list[str] = []
        self._runner: web.AppRunner | None = None
        self.port = 0

    async def start(self) -> None:
        app = web.Application()

        async def handler(request: web.Request) -> web.WebSocketResponse:
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            self.accepted.append(ws)
            async for msg in ws:
                self.received.append(msg.data)
            return ws

        app.router.add_get("/v1/accounts/{account_id}/agent/listen", handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = self._runner.addresses[0][1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        for ws in self.accepted:
            if not ws.closed:
                await ws.close()
        if self._runner:
            await self._runner.cleanup()


async def _client(server: _Server, seen: list[tuple[int, str]] | None = None) -> ControlWebSocket:
    async def _noop(_data: dict) -> None:
        return None

    return ControlWebSocket(
        base_url=server.base_url,
        api_key="key",
        account_id="AC1",
        number="07012345678",
        on_call_incoming=_noop,
        on_call_ended=_noop,
        on_terminal_close=(lambda code, reason: seen.append((code, reason))) if seen is not None else None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [CLOSE_REPLACED, CLOSE_OWNERSHIP_LOST])
async def test_does_not_reconnect_after_terminal_close(code: int) -> None:
    server = _Server()
    await server.start()
    seen: list[tuple[int, str]] = []
    ws = await _client(server, seen)
    task = asyncio.create_task(ws.connect())
    try:
        await ws.wait_connected(timeout=5)
        assert len(server.accepted) == 1

        await server.accepted[0].close(code=code, message=b"handover")
        # 재연결 backoff 는 1초에서 시작한다 — 일어날 거라면 이 창 안에 일어난다.
        await asyncio.sleep(1.8)

        assert len(server.accepted) == 1, "인계 통지를 받고도 재연결했다 — 새 프로세스를 밀어낸다"
        # 사유까지 고정한다. aiohttp 의 `async for` 는 CLOSE 를 몸통에 전달하지 않아
        # 사유가 조용히 빈 문자열이 되는데, 코드만 보면 통과해 그 구멍이 안 보인다.
        assert seen == [(code, "handover")]
    finally:
        await ws.close()
        task.cancel()
        await server.stop()


@pytest.mark.asyncio
async def test_still_reconnects_when_gateway_drains() -> None:
    """1001 은 자리를 뺏긴 게 아니라 그 게이트웨이가 빠지는 것 — 다시 붙어야 한다."""
    server = _Server()
    await server.start()
    seen: list[tuple[int, str]] = []
    ws = await _client(server, seen)
    task = asyncio.create_task(ws.connect())
    try:
        await ws.wait_connected(timeout=5)
        await server.accepted[0].close(code=1001, message=b"gateway draining")
        await asyncio.sleep(1.8)

        assert len(server.accepted) >= 2
        assert seen == []
    finally:
        await ws.close()
        task.cancel()
        await server.stop()


# ── lame duck ─────────────────────────────────────────────────────────────
# 물러나기로 한 프로세스가 재연결하면, 그 사이 자리를 받은 후임을 밀어낸다. 후임은 SIGTERM 도
# 안 받았는데 물러나고, 우리는 곧 스스로 죽는다 — 자리가 통째로 빈다.
#
# 예전에는 drain() 이 control 을 **먼저 닫는** 것이 그 방어였다(닫으면 재연결 경로가 없다).
# 인계 대기는 자리를 들고 기다리는 것이라 그 방어가 사라진다. 그래서 대체물이 필요하다.


@pytest.mark.asyncio
async def test_lame_duck_does_not_reconnect_on_gateway_drain() -> None:
    """게이트웨이 롤링(1001)에도 재연결하지 않는다 — 평소에는 재연결하는 코드다."""
    server = _Server()
    await server.start()
    ws = await _client(server)
    task = asyncio.create_task(ws.connect())
    try:
        await ws.wait_connected(timeout=5)
        ws.enter_lame_duck()

        await server.accepted[0].close(code=1001, message=b"gateway draining")
        await asyncio.sleep(1.8)

        assert len(server.accepted) == 1, "물러나는 중에 재연결했다 — 후임을 밀어낸다"
    finally:
        await ws.close()
        task.cancel()
        await server.stop()


@pytest.mark.asyncio
async def test_lame_duck_marks_itself_retiring_in_the_url() -> None:
    """재연결하는 경로가 남아 있더라도 자리를 뺏지는 않게 한다(2차 방어)."""
    server = _Server()
    await server.start()
    ws = await _client(server)
    try:
        assert "role=" not in ws._url
        ws.enter_lame_duck()
        assert "role=retiring" in ws._url
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_agent_retired_event_enters_lame_duck_without_closing() -> None:
    """인계 통지는 **연결을 남긴 채** 온다 — 진행 중 통화의 종료 이벤트가 이 연결로 돌아온다."""
    server = _Server()
    await server.start()
    retired: list[str] = []

    async def _noop(_data: dict) -> None:
        return None

    ws = ControlWebSocket(
        base_url=server.base_url,
        api_key="key",
        account_id="AC1",
        number="07012345678",
        on_call_incoming=_noop,
        on_call_ended=_noop,
        on_retired=retired.append,
    )
    task = asyncio.create_task(ws.connect())
    try:
        await ws.wait_connected(timeout=5)
        await server.accepted[0].send_str(
            '{"event":"agent.retired","number":"07012345678","reason":"replaced"}'
        )
        for _ in range(50):
            if retired:
                break
            await asyncio.sleep(0.02)

        assert retired == ["replaced"]
        assert ws.lame_duck is True
        assert not server.accepted[0].closed, "인계 통지에 연결을 끊으면 종료 이벤트를 못 받는다"
    finally:
        await ws.close()
        task.cancel()
        await server.stop()


# ── 재연결 창의 전환 ───────────────────────────────────────────────────────
# 끊긴 뒤 다시 붙기까지(1초~) `_ws` 는 None 이다. Agent 쪽 검사(`_control_ws is None`)는 객체가 살아
# 있는 이 창을 못 막아, 전환이 사용자 툴 밖으로 맨 AttributeError 를 던졌다(clawops#1978 측정).


async def test_transfer_during_reconnect_window_fails_clearly_then_works_again() -> None:
    server = _Server()
    await server.start()
    ws = await _client(server)
    task = asyncio.create_task(ws.connect())
    try:
        await ws.wait_connected(timeout=5)
        await server.accepted[0].close(code=1001, message=b"gateway draining")
        for _ in range(100):
            if ws._ws is None:
                break
            await asyncio.sleep(0.01)
        assert ws._ws is None, "재연결 창에 들어가지 못했다"

        with pytest.raises(AgentError, match="transfer 불가"):
            await asyncio.wait_for(ws.request_transfer("CA1", {"to": "01011112222"}), timeout=1)
        assert ws._pending_transfers == {}
        assert ws._pending_by_call == {}

        # 창이 지나면 같은 객체로 전환이 다시 된다 — 실패가 연결 상태를 망가뜨리지 않았다.
        # wait_connected() 는 여기서 못 쓴다 — 재연결 대기 중에도 이전 연결의 완료 표시가 남아 있다.
        for _ in range(500):
            if len(server.accepted) >= 2 and ws._ws is not None:
                break
            await asyncio.sleep(0.01)
        transfer = asyncio.create_task(ws.request_transfer("CA1", {"to": "01011112222"}))
        await asyncio.sleep(0.05)
        assert len(ws._pending_transfers) == 1
        assert [json.loads(m)["event"] for m in server.received] == ["call.transfer"]
        await ws._dispatch(
            '{"event": "call.transfer.completed", "callId": "CA1", "transfer": {"status": "completed"}}'
        )
        assert (await asyncio.wait_for(transfer, timeout=1))["status"] == "completed"
    finally:
        await ws.close()
        task.cancel()
        await server.stop()
