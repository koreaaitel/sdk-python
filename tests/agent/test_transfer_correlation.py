# tests/agent/test_transfer_correlation.py
"""전환 대기의 correlation 규약.

2026-08-12 사고: transfer_call 의 브리지가 40초를 넘기면 대기가 취소되고, 뒤늦게 도착한 완료
이벤트가 취소된 future 에 set_result 를 호출해 InvalidStateError 를 던졌다. 그 예외가 수신 루프
밖으로 새어 제어 연결 태스크가 죽고 재접속이 영영 일어나지 않았다(16시간 수신 불가).

여기서 고정하는 불변식:
  ① 대기는 **요청 단위**로 키잉된다 — 같은 통화에서 전환을 두 번 해도 각자 자기 결과를 받는다
  ② 클라이언트 대기 상한은 브리지 길이와 무관하다(서버가 결과를 보낸다는 계약을 신뢰)
  ③ 통화 종료는 정리 신호이며, 순서 역전을 유예로 흡수한다
  ④ 이벤트 처리 예외가 연결을 죽이지 않고, 예외 종류와 무관하게 재접속한다
  ⑤ 결과가 중복/지연 도착해도 예외가 나지 않는다
  ⑥ 어떤 경로로 끝나든 pending 이 남지 않는다
"""
from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest

from clawops._exceptions import AgentConnectionError
from clawops.agent import _control_ws as cw
from clawops.agent._control_ws import ControlWebSocket


class FakeWs:
    """send_str 을 기록하는 최소 WS. 수신은 테스트가 직접 _dispatch 로 넣는다."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_str(self, s: str) -> None:
        self.sent.append(json.loads(s))

    async def close(self) -> None:
        self.closed = True


async def _noop(_data: dict) -> None:
    return None


def make_cws() -> ControlWebSocket:
    cws = ControlWebSocket(
        base_url="https://api.claw-ops.com",
        api_key="sk_test",
        account_id="AC1",
        number="07000000000",
        on_call_incoming=_noop,
        on_call_ended=_noop,
    )
    cws._ws = FakeWs()  # type: ignore[assignment]
    return cws


def result_event(call_id: str, request_id: str | None, status: str = "completed") -> dict:
    ev = {
        "event": f"call.transfer.{'completed' if status == 'completed' else 'failed'}",
        "callId": call_id,
        "transfer": {"status": status, "duration": 61},
    }
    if request_id:
        ev["requestId"] = request_id
    return ev


# ── ① 요청 단위 상관 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_id_is_sent_to_server():
    cws = make_cws()
    task = asyncio.create_task(cws.request_transfer("CA1", {"to": "01011112222", "timeout": 30}))
    await asyncio.sleep(0)

    sent = cws._ws.sent[0]  # type: ignore[union-attr]
    assert sent["event"] == "call.transfer"
    assert sent["callId"] == "CA1"
    request_id = sent["transfer"]["requestId"]
    assert request_id, "서버가 echo 할 상관 ID 를 보내야 한다"
    # 호출자가 준 파라미터는 그대로 유지된다.
    assert sent["transfer"]["to"] == "01011112222"
    assert sent["transfer"]["timeout"] == 30

    cws._on_transfer_event(result_event("CA1", request_id))
    assert (await task)["status"] == "completed"


@pytest.mark.asyncio
async def test_two_transfers_same_call_each_resolve_with_own_result():
    """return 모드는 같은 통화에서 전환을 다시 탄다(실측 8회). 예전 구현은 여기서 1차를 잃었다.

    ⚠️ 상관이 callId 로 되돌아가면 두 대기가 서로를 덮어써 **영영 resolve 되지 않는다**.
    타임아웃 없이 두면 그 회귀가 실패가 아니라 hang 으로 나타나 CI 를 막는다 — 명시적 실패로 만든다.
    """
    cws = make_cws()

    t1 = asyncio.create_task(cws.request_transfer("CA1", {"to": "0101"}))
    await asyncio.sleep(0)
    t2 = asyncio.create_task(cws.request_transfer("CA1", {"to": "0102"}))
    await asyncio.sleep(0)

    rid1 = cws._ws.sent[0]["transfer"]["requestId"]  # type: ignore[union-attr]
    rid2 = cws._ws.sent[1]["transfer"]["requestId"]  # type: ignore[union-attr]
    assert rid1 != rid2, "요청마다 다른 상관 ID 여야 한다 — 같으면 서로를 덮어쓴다"

    # 2차부터 결과가 와도 1차가 오염되지 않는다.
    cws._on_transfer_event(result_event("CA1", rid2, "failed"))
    cws._on_transfer_event(result_event("CA1", rid1, "completed"))

    r1 = await asyncio.wait_for(t1, timeout=1.0)
    r2 = await asyncio.wait_for(t2, timeout=1.0)
    assert r1["status"] == "completed"
    assert r2["status"] == "failed"
    assert cws._pending_transfers == {}
    assert cws._pending_by_call == {}


@pytest.mark.asyncio
async def test_unknown_request_id_is_ignored():
    cws = make_cws()
    task = asyncio.create_task(cws.request_transfer("CA1", {"to": "0101"}))
    await asyncio.sleep(0)
    rid = cws._ws.sent[0]["transfer"]["requestId"]  # type: ignore[union-attr]

    # 다른 요청의 결과가 흘러들어와도 이 대기를 깨우지 않는다.
    cws._on_transfer_event(result_event("CA1", "req-someone-else"))
    assert not task.done()

    cws._on_transfer_event(result_event("CA1", rid))
    assert (await task)["status"] == "completed"


# ── 하위호환 폴백 ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_falls_back_to_call_id_when_server_omits_request_id():
    """requestId 를 echo 하지 않는 구 서버와 섞이는 전환 기간용 폴백."""
    cws = make_cws()
    task = asyncio.create_task(cws.request_transfer("CA1", {"to": "0101"}))
    await asyncio.sleep(0)

    cws._on_transfer_event(result_event("CA1", None))
    assert (await task)["status"] == "completed"


@pytest.mark.asyncio
async def test_call_id_fallback_does_not_guess_between_multiple_waiters():
    """대기가 둘이면 callId 만으로는 짝지을 수 없다 — 엉뚱한 쪽을 깨우지 않는다."""
    cws = make_cws()
    t1 = asyncio.create_task(cws.request_transfer("CA1", {"to": "0101"}))
    await asyncio.sleep(0)
    t2 = asyncio.create_task(cws.request_transfer("CA1", {"to": "0102"}))
    await asyncio.sleep(0)

    cws._on_transfer_event(result_event("CA1", None))
    assert not t1.done() and not t2.done()

    for rid in (cws._ws.sent[0], cws._ws.sent[1]):  # type: ignore[union-attr]
        cws._on_transfer_event(result_event("CA1", rid["transfer"]["requestId"]))
    await asyncio.gather(t1, t2)


# ── ② 대기 상한이 브리지 길이와 무관 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_wait_is_not_derived_from_timeout_param():
    """사고의 뿌리 — 예전에는 timeout+10 으로 기다려 57초 브리지에서 취소됐다."""
    assert cw.TRANSFER_RESULT_MAX_WAIT_S > 600, "브리지 길이에 연동된 상한이면 안 된다"

    cws = make_cws()
    task = asyncio.create_task(cws.request_transfer("CA1", {"to": "0101", "timeout": 1}))
    await asyncio.sleep(0)
    rid = cws._ws.sent[0]["transfer"]["requestId"]  # type: ignore[union-attr]

    # timeout=1 이지만 한참 뒤 결과가 와도 정상 반환된다.
    await asyncio.sleep(0.05)
    cws._on_transfer_event(result_event("CA1", rid))
    assert (await task)["status"] == "completed"


# ── ③ 통화 종료는 정리 신호 + 순서 역전 흡수 ────────────────────────────────


@pytest.mark.asyncio
async def test_late_result_after_call_ended_still_resolves(monkeypatch):
    """call.ended 가 결과보다 먼저 닿아도 유예 안에 온 결과로 정상 resolve 된다."""
    monkeypatch.setattr(cw, "TRANSFER_LATE_ARRIVAL_GRACE_S", 0.2)
    cws = make_cws()
    task = asyncio.create_task(cws.request_transfer("CA1", {"to": "0101"}))
    await asyncio.sleep(0)
    rid = cws._ws.sent[0]["transfer"]["requestId"]  # type: ignore[union-attr]

    await cws._dispatch(json.dumps({"event": "call.ended", "callId": "CA1", "duration": 91}))
    assert not task.done(), "즉시 취소하면 뒤따라오는 정상 결과를 버린다"

    cws._on_transfer_event(result_event("CA1", rid))
    assert (await task)["status"] == "completed"


@pytest.mark.asyncio
async def test_pending_is_cleaned_when_result_never_arrives(monkeypatch):
    monkeypatch.setattr(cw, "TRANSFER_LATE_ARRIVAL_GRACE_S", 0.05)
    cws = make_cws()
    task = asyncio.create_task(cws.request_transfer("CA1", {"to": "0101"}))
    await asyncio.sleep(0)

    await cws._dispatch(json.dumps({"event": "call.ended", "callId": "CA1", "duration": 5}))
    await asyncio.sleep(0.15)

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cws._pending_transfers == {}
    assert cws._pending_by_call == {}


# ── ④ 이벤트 처리 예외가 연결을 죽이지 않는다 ───────────────────────────────


@pytest.mark.asyncio
async def test_handler_exception_is_isolated_from_connection():
    """핸들러가 던져도 _dispatch 호출부가 잡는다 — 여기서는 계약(예외가 전파됨)만 고정하고,
    연결 유지는 connect() 루프의 try/except 가 담당한다."""

    async def boom(_data: dict) -> None:
        raise RuntimeError("handler blew up")

    cws = ControlWebSocket(
        base_url="https://api.claw-ops.com",
        api_key="sk",
        account_id="AC1",
        number="0700",
        on_call_incoming=boom,
        on_call_ended=_noop,
    )
    with pytest.raises(RuntimeError):
        await cws._dispatch(json.dumps({"event": "call.incoming", "callId": "CA1"}))


def test_reconnect_loop_catches_any_exception():
    """예외 종류로 재접속 여부를 가르지 않는다. 예전에는 ClientError/OSError 만 잡아
    InvalidStateError 에 태스크가 죽었다."""
    import inspect

    src = inspect.getsource(ControlWebSocket.connect)
    assert "except (aiohttp.ClientError, OSError)" not in src, "좁은 except 가 되살아났다"
    assert "except Exception" in src
    assert "except asyncio.CancelledError" in src, "취소는 재접속 대상이 아니다"


# ── ⑤ 멱등성 ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_result_does_not_raise():
    cws = make_cws()
    task = asyncio.create_task(cws.request_transfer("CA1", {"to": "0101"}))
    await asyncio.sleep(0)
    rid = cws._ws.sent[0]["transfer"]["requestId"]  # type: ignore[union-attr]

    cws._on_transfer_event(result_event("CA1", rid))
    # 같은 결과가 또 와도 예외가 나면 안 된다 — 그 예외가 연결을 죽였다.
    cws._on_transfer_event(result_event("CA1", rid))
    assert (await task)["status"] == "completed"


@pytest.mark.asyncio
async def test_result_after_cancelled_wait_does_not_raise():
    """사고 재현 시나리오 — 취소된 대기에 결과가 도착한다.

    이 경로는 `request_transfer` 의 finally 정리가 이미 막는다(취소 시 pending 에서 빠진다).
    아래 test_done_guard_… 가 그 정리가 없었을 때의 마지막 방어선을 따로 고정한다.
    """
    cws = make_cws()
    task = asyncio.create_task(cws.request_transfer("CA1", {"to": "0101"}))
    await asyncio.sleep(0)
    rid = cws._ws.sent[0]["transfer"]["requestId"]  # type: ignore[union-attr]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cws._pending_transfers == {}, "취소 시 finally 가 상관을 놓아야 한다"

    # InvalidStateError 가 나면 수신 루프가 죽는다.
    cws._on_transfer_event(result_event("CA1", rid))


@pytest.mark.asyncio
async def test_done_guard_absorbs_result_for_already_finished_future():
    """pending 에 남아 있는데 이미 끝난 future — set_result 가 InvalidStateError 를 던지는 상태.

    finally 정리가 그 조합을 만들지 않게 하고 있지만, 그 보장은 호출 순서에 의존한다.
    수신 루프를 죽이는 예외가 이 한 줄에서 나왔던 사고라 마지막 방어선을 직접 고정한다.
    """
    cws = make_cws()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    cws._pending_transfers["REQ-X"] = future
    cws._pending_by_call["CA1"] = {"REQ-X"}
    future.cancel()  # pending 에 남은 채 취소된 상태

    cws._on_transfer_event(result_event("CA1", "REQ-X"))  # 예외가 나면 안 된다

    assert cws._pending_transfers == {}, "그래도 상관은 놓아야 한다"


@pytest.mark.asyncio
async def test_done_guard_absorbs_second_result_for_resolved_future():
    cws = make_cws()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    cws._pending_transfers["REQ-Y"] = future
    cws._pending_by_call["CA1"] = {"REQ-Y"}
    future.set_result({"status": "completed"})

    cws._on_transfer_event(result_event("CA1", "REQ-Y"))  # 예외가 나면 안 된다
    assert await future == {"status": "completed"}


@pytest.mark.asyncio
async def test_progress_events_do_not_wake_the_waiter():
    cws = make_cws()
    task = asyncio.create_task(cws.request_transfer("CA1", {"to": "0101"}))
    await asyncio.sleep(0)
    rid = cws._ws.sent[0]["transfer"]["requestId"]  # type: ignore[union-attr]

    for name in ("call.transfer.started", "call.transfer.connected"):
        cws._on_transfer_event({"event": name, "callId": "CA1", "requestId": rid})
    assert not task.done()

    cws._on_transfer_event(result_event("CA1", rid))
    await task


# ── ⑥ 누수 없음 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_pending_left_after_normal_result():
    cws = make_cws()
    task = asyncio.create_task(cws.request_transfer("CA1", {"to": "0101"}))
    await asyncio.sleep(0)
    rid = cws._ws.sent[0]["transfer"]["requestId"]  # type: ignore[union-attr]
    cws._on_transfer_event(result_event("CA1", rid))
    await task

    assert cws._pending_transfers == {}
    assert cws._pending_by_call == {}


@pytest.mark.asyncio
async def test_no_pending_left_after_send_failure():
    """전송 자체가 실패해도 상관이 남지 않는다."""
    cws = make_cws()

    async def boom(_s: str) -> None:
        raise aiohttp.ClientError("socket gone")

    cws._ws.send_str = boom  # type: ignore[assignment,union-attr]
    with pytest.raises(AgentConnectionError, match="transfer 불가"):
        await cws.request_transfer("CA1", {"to": "0101"})

    assert cws._pending_transfers == {}
    assert cws._pending_by_call == {}


@pytest.mark.asyncio
async def test_send_to_closing_transport_fails_clearly():
    """aiohttp 는 닫히는 소켓에 쓰면 ConnectionResetError 계열을 던진다."""
    cws = make_cws()

    async def boom(_s: str) -> None:
        raise ConnectionResetError("Cannot write to closing transport")

    cws._ws.send_str = boom  # type: ignore[assignment,union-attr]
    with pytest.raises(AgentConnectionError, match="transfer 불가"):
        await cws.request_transfer("CA1", {"to": "0101"})

    assert cws._pending_transfers == {}
    assert cws._pending_by_call == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["none", "closed"])
async def test_transfer_without_connection_sends_nothing_and_leaves_no_pending(state: str):
    """재연결 창(`_ws` None)과 닫히는 중(`closed`) 모두 대기를 만들기 전에 실패한다."""
    cws = make_cws()
    if state == "none":
        cws._ws = None
    else:
        cws._ws.closed = True  # type: ignore[union-attr]

    # 회귀하면 실패가 아니라 결과 대기(2시간)로 나타난다 — 명시적 실패로 만든다.
    with pytest.raises(AgentConnectionError, match="transfer 불가"):
        await asyncio.wait_for(cws.request_transfer("CA1", {"to": "0101"}), timeout=1)

    if state == "closed":
        assert cws._ws.sent == []  # type: ignore[union-attr]
    assert cws._pending_transfers == {}
    assert cws._pending_by_call == {}


@pytest.mark.asyncio
async def test_close_cancels_pending_and_timers():
    cws = make_cws()
    task = asyncio.create_task(cws.request_transfer("CA1", {"to": "0101"}))
    await asyncio.sleep(0)

    await cws.close()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cws._pending_transfers == {}
    assert cws._pending_by_call == {}
    assert cws._cleanup_timers == set()
