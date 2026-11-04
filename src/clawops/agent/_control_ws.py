"""Control WebSocket: ClawOps 서버에 대한 상시 연결 관리.

Agent가 서버에 역방향으로 연결하여 인바운드 콜 알림을 수신한다.
자동 재연결(exponential backoff) 포함.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable, Awaitable
from urllib.parse import quote

import aiohttp

from .._exceptions import AgentAuthRejectedError, AgentConnectionError

log = logging.getLogger("clawops.agent")

INITIAL_RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 30.0

# 다른 프로세스가 이 번호의 control 연결을 넘겨받았다(무중단 배포).
CLOSE_REPLACED = 4409
# 이 번호가 더 이상 이 계정 것이 아니다(반납/재배정).
CLOSE_OWNERSHIP_LOST = 4403

# 이 코드로 닫혔으면 재연결은 소용없는 게 아니라 **틀린 것**이다.
#
# control 연결은 번호당 하나뿐이다. 서버가 그 자리를 새 프로세스에 넘겼는데 우리가 재연결하면
# 자기 연결을 되찾는 게 아니라 **방금 인계받은 프로세스를 밀어낸다**. 그러면 그쪽이 다시
# 재연결해 우리를 밀어내고, 배포로 겹치는 창 내내 자리를 주고받는다. 그 핑퐁은 서버의
# reconnect throttle 로 번져 번호가 분 단위로 격리된다(2026-05-21·05-28 ARI freeze 와 같은
# 트리거). 재연결이 무중단 인계를 장애로 바꾼다.
#
# 나머지 코드(1001 gateway draining, 비정상 절단 등)는 계속 재연결한다 — 그때는 자리가
# 실제로 비어 있고 그 자리의 주인이 우리다.
NON_RETRYABLE_CLOSE_CODES = frozenset({CLOSE_REPLACED, CLOSE_OWNERSHIP_LOST})

# 핸드셰이크를 이 상태로 거절당하면 다시 시도해도 결과가 같다 — 키(401·403)·번호(403)·요청 형식(400).
# 2026-10-28 서버 실측: 폐기된 키·회수된 번호로 붙던 에이전트 21곳이 403 을 30초마다 끝없이 다시 시도하고
# 있었다(고객은 모른 채). 일시적 실패(429 재연결 제한·5xx·네트워크)는 지금처럼 재연결한다.
REJECTED_HANDSHAKE_STATUSES = frozenset({400, 401, 403})


def _rejection_message(status: int, code: str | None, *, account_id: str, number: str) -> str:
    if code == "api_key_invalid":
        return (
            "API 키가 유효하지 않습니다(폐기·만료됐거나 다른 계정의 키). "
            f"계정 {account_id} 의 새 키를 발급해 설정하세요."
        )
    if code == "api_key_missing":
        return "API 키가 전달되지 않았습니다. api_key 또는 CLAWOPS_API_KEY 를 확인하세요."
    if code == "number_not_owned":
        return (
            f"번호 {number} 는 계정 {account_id} 의 번호가 아닙니다(반납·회수됐거나 다른 계정 번호). "
            "콘솔에서 번호를 확인하세요."
        )
    if code == "number_missing":
        return "번호가 전달되지 않았습니다. from_ 을 확인하세요."
    return f"서버가 연결을 거절했습니다(HTTP {status}). API 키와 번호({number})를 확인하세요."

# 전환 결과를 기다리는 **방어선**. 정상 경로의 일부가 아니다 — 서버가 대상 응답 대기(transfer
# 파라미터 `timeout`)를 관리하고 결과 이벤트를 반드시 보내므로, 클라이언트는 브리지가 얼마나
# 길든 기다린다. 이 값에 도달하는 것은 "서버가 계약을 어겼다" 는 뜻이라 경고를 남긴다.
#
# 이 상한이 브리지 길이에 연동돼 있던 것이 2026-08-12 사고의 뿌리였다: timeout+10(=40초)로
# 기다리다 57초 브리지에서 취소되고, 뒤늦은 완료 이벤트가 취소된 future 를 건드려 제어 연결이
# 죽었다(16시간 수신 불가).
TRANSFER_RESULT_MAX_WAIT_S = 7200.0

# 통화 종료 통지를 받은 뒤 전환 대기를 정리하기까지의 유예. 즉시 정리하지 않는 이유는
# 이벤트 도착 순서가 보장되지 않기 때문이다 — 서버는 결과와 call.ended 를 각각 독립 HTTP 요청으로
# 릴레이하므로(call-engine agent-gw-relay: await 없는 POST) call.ended 가 먼저 닿을 수 있다.
# 순서를 보장하려 애쓰는 대신 이 유예로 흡수한다.
TRANSFER_LATE_ARRIVAL_GRACE_S = 2.0

TRANSFER_NO_CONNECTION_MESSAGE = "transfer 불가: control 연결이 끊겨 있다(재연결 중이거나 연결이 종료됐다)"


def build_control_ws_url(
    *, base_url: str, account_id: str, number: str, role: str | None = None
) -> str:
    scheme = "wss" if base_url.startswith("https") else "ws"
    host = base_url.replace("https://", "").replace("http://", "").rstrip("/")
    url = f"{scheme}://{host}/v1/accounts/{account_id}/agent/listen?number={quote(number)}"
    # role=retiring = "나는 이미 물러난 프로세스다. 자리를 뺏지 말라."
    #   lame duck 이 된 뒤에는 재연결하지 않는 것이 1차 방어지만, 그 방어를 우회하는 경로가
    #   남아 있을 수 있다(하위 레이어 재시도 등). 그때 이 플래그가 없으면 SIGTERM 도 안 받은
    #   후임을 밀어낸다 — 막으려던 핑퐁이 그대로 재현된다.
    if role:
        url += f"&role={quote(role)}"
    return url


class ControlWebSocket:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        account_id: str,
        number: str,
        on_call_incoming: Callable[[dict[str, Any]], Awaitable[None]],
        on_call_ended: Callable[[dict[str, Any]], Awaitable[None]],
        on_call_outbound_ready: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_call_ringing: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_call_failed: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_terminal_close: Callable[[int, str], None] | None = None,
        on_retired: Callable[[str], None] | None = None,
        on_rejected: Callable[[AgentAuthRejectedError], None] | None = None,
    ) -> None:
        self._url_parts = {"base_url": base_url, "account_id": account_id, "number": number}
        self._url = build_control_ws_url(base_url=base_url, account_id=account_id, number=number)
        self._api_key = api_key
        self._on_call_incoming = on_call_incoming
        self._on_call_ended = on_call_ended
        self._on_call_outbound_ready = on_call_outbound_ready
        self._on_call_ringing = on_call_ringing
        self._on_call_failed = on_call_failed
        self._on_terminal_close = on_terminal_close
        self._on_retired = on_retired
        self._on_rejected = on_rejected
        # lame duck = 이 프로세스는 물러나는 중이다. 어떤 이유로 끊겨도 **재연결하지 않는다**.
        #   게이트웨이 롤링(1001)이나 네트워크 blip(1006)에 재연결하면 그 사이 자리를 받은
        #   후임을 밀어낸다. 후임은 SIGTERM 도 안 받았는데 물러나고, 우리는 곧 스스로 죽는다.
        self._lame_duck = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._connected = asyncio.Event()
        # 핸드셰이크 거절(재연결 안 함). wait_connected() 가 타임아웃을 기다리지 않고 바로 깨어나 던진다.
        self._rejected: AgentAuthRejectedError | None = None
        self._rejected_event = asyncio.Event()
        # 전환 대기는 **요청 단위**로 키잉한다(callId 아님). return 모드는 실패 후 AI 로 돌아와
        # 같은 통화에서 전환을 다시 타므로(서버 transfer-handler 주석: 실측 8회 재시도), callId 로
        # 키잉하면 2차 요청이 1차 대기를 덮어써 1차가 영영 resolve 되지 않는다.
        self._pending_transfers: dict[str, asyncio.Future] = {}  # requestId → future
        self._connection_lost_waiters: set[asyncio.Future] = set()
        # callId → 그 통화에서 대기 중인 requestId 집합. 통화 종료 시 정리 대상을 찾고,
        # requestId 를 echo 하지 않는 구 서버의 이벤트를 매핑하는 폴백에 쓴다.
        self._pending_by_call: dict[str, set[str]] = {}
        self._cleanup_timers: set[asyncio.TimerHandle] = set()

    def enter_lame_duck(self) -> None:
        """이제부터 끊겨도 재연결하지 않는다. 되돌릴 수 없다."""
        if self._lame_duck:
            return
        self._lame_duck = True
        # 이 뒤에 재연결하는 경로가 남아 있다면, 적어도 자리를 뺏지는 않게 한다.
        self._url = build_control_ws_url(**self._url_parts, role="retiring")

    @property
    def lame_duck(self) -> bool:
        return self._lame_duck

    @property
    def rejected(self) -> AgentAuthRejectedError | None:
        return self._rejected

    async def wait_connected(self, timeout: float = 10.0) -> None:
        """Control WS 연결이 완료될 때까지 대기한다.

        서버가 핸드셰이크를 거절하면(키·번호) 타임아웃을 기다리지 않고 ``AgentAuthRejectedError`` 를 던진다.
        """
        if self._rejected is not None:
            raise self._rejected
        connected = asyncio.ensure_future(self._connected.wait())
        rejected = asyncio.ensure_future(self._rejected_event.wait())
        try:
            done, _ = await asyncio.wait(
                {connected, rejected}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for fut in (connected, rejected):
                fut.cancel()
        if self._rejected is not None:
            raise self._rejected
        if not done:
            raise asyncio.TimeoutError()

    async def connect(self) -> None:
        self._running = True
        delay = INITIAL_RECONNECT_DELAY

        while self._running:
            try:
                self._connected.clear()
                close_reason = ""
                self._session = aiohttp.ClientSession()
                self._ws = await self._session.ws_connect(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    heartbeat=30.0,
                )
                self._connected.set()
                log.info(f"Control WS connected: {self._url}")
                delay = INITIAL_RECONNECT_DELAY

                # `async for` 를 쓰지 않는다. aiohttp 의 __anext__ 는 CLOSE/CLOSING/CLOSED 에
                # StopAsyncIteration 을 내므로 CLOSE 메시지가 루프 몸통에 **도달하지 않는다** —
                # 그러면 close_reason 이 영영 빈 문자열이라 인계 통지에 사유가 실리지 않는다.
                while True:
                    msg = await self._ws.receive()
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        # 이벤트 처리 예외를 연결 수명과 분리한다. 핸들러 하나가 던진 예외가
                        # 이 루프 밖으로 새면 연결 태스크가 통째로 죽는다 — 2026-08-12 사고가
                        # 정확히 그것이었다(취소된 future 의 InvalidStateError).
                        try:
                            await self._dispatch(msg.data)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            log.exception("Control WS 이벤트 처리 실패 — 연결은 유지한다")
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        if msg.type == aiohttp.WSMsgType.CLOSE:
                            close_reason = msg.extra or ""
                        break

                # 루프를 벗어났다 = 서버가 닫았다. **왜** 닫혔는지가 재연결 여부를 가른다.
                close_code = self._ws.close_code if self._ws else None
                if close_code in NON_RETRYABLE_CLOSE_CODES:
                    self._running = False
                    log.info(
                        f"Control WS 종료 통지 ({close_code} {close_reason}) — "
                        "재연결하지 않는다: 이 번호는 이제 다른 프로세스가 맡는다"
                    )
                    if self._on_terminal_close:
                        self._on_terminal_close(close_code, close_reason)

            except asyncio.CancelledError:
                # close() 나 태스크 취소 — 재접속하지 않는다.
                raise
            except aiohttp.WSServerHandshakeError as e:
                if e.status not in REJECTED_HANDSHAKE_STATUSES:
                    # 429·5xx 등 — 자리가 실제로 비어 있을 수 있다. 재연결한다.
                    log.warning(f"Control WS error: {type(e).__name__}: {e}")
                else:
                    code = e.headers.get("X-ClawOps-Error-Code") if e.headers else None
                    err = AgentAuthRejectedError(
                        _rejection_message(
                            e.status,
                            code,
                            account_id=self._url_parts["account_id"],
                            number=self._url_parts["number"],
                        ),
                        status=e.status,
                        code=code,
                    )
                    self._running = False
                    self._rejected = err
                    self._rejected_event.set()
                    log.error(f"Control WS 거절({e.status} {code or '-'}) — 재연결하지 않는다: {err.message}")
                    if self._on_rejected:
                        try:
                            self._on_rejected(err)
                        except Exception:
                            log.exception("on_rejected 콜백 실패")
            except Exception as e:
                # 예외 종류로 재접속 여부를 가르지 않는다. 예전에는 aiohttp.ClientError/OSError
                # 만 잡아, 그 밖의 예외(InvalidStateError 등)에 재접속 없이 태스크가 죽었다.
                if "CERTIFICATE_VERIFY_FAILED" in str(e):
                    log.error(
                        "SSL 인증서 검증에 실패했습니다. "
                        "'pip install --upgrade certifi'를 실행해 보세요. "
                        "자세한 해결 방법: "
                        "https://github.com/koreaaitel/sdk-python/blob/main/docs/agent/troubleshooting.md#ssl-인증서-에러-sslcertverificationerror"
                    )
                log.warning(f"Control WS error: {type(e).__name__}: {e}")
            finally:
                self._fire_connection_lost()
                if self._ws and not self._ws.closed:
                    await self._ws.close()
                if self._session:
                    await self._session.close()
                self._ws = None
                self._session = None

            if self._lame_duck:
                # 물러나는 중이다. 자리는 이미 남의 것이거나 곧 남의 것이 된다.
                log.info("Control WS 종료 — 물러나는 중이라 재연결하지 않는다")
                self._running = False
                return
            if self._running:
                log.info(f"Control WS reconnecting in {delay:.1f}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY)

    async def _dispatch(self, raw: str) -> None:
        """수신 메시지 하나를 해당 핸들러로 보낸다. 예외는 호출부가 격리한다."""
        data = json.loads(raw)
        event = data.get("event")
        if event == "call.incoming":
            await self._on_call_incoming(data)
        elif event == "call.ended":
            # 종료 통지는 전환 대기의 **정리 신호**다(결과를 만들어내는 신호가 아니다).
            # 순서가 보장되지 않으므로 유예를 두고 정리한다.
            self._schedule_transfer_cleanup(data.get("callId"))
            await self._on_call_ended(data)
        elif event == "call.outbound_ready" and self._on_call_outbound_ready:
            await self._on_call_outbound_ready(data)
        elif event == "call.ringing" and self._on_call_ringing:
            await self._on_call_ringing(data)
        elif event == "call.failed" and self._on_call_failed:
            await self._on_call_failed(data)
        elif event == "agent.retired":
            # 자리를 다른 프로세스가 넘겨받았다. 4409 close 와 같은 뜻인데 **연결은 살아 있다** —
            # 진행 중이던 통화의 종료 이벤트가 이 연결로 돌아오게 하려고 서버가 남겨 둔 것이다.
            # 그래서 여기서 끊으면 안 된다. 통화를 마치고 나가면 서버가 닫아 준다.
            self._lame_duck = True
            if self._on_retired:
                self._on_retired(str(data.get("reason") or ""))
        elif event == "agent.active":
            # 앞선 자리 주인이 사라져 우리가 다시 자리를 받았다(승격). 이미 물러나기로 한
            # 프로세스라면 되돌리지 않는다 — 곧 죽을 프로세스가 자리를 들고 있으면 안 된다.
            log.info("서버가 이 연결을 다시 자리 주인으로 올렸다 (승격)")
        elif event in (
            "call.transfer.started",
            "call.transfer.connected",
            "call.transfer.completed",
            "call.transfer.failed",
        ):
            self._on_transfer_event(data)

    def connection_lost(self) -> asyncio.Future:
        """지금 연결이 끊기면 완료되는 future. 이미 끊겨 있으면 완료된 채로 돌려준다.

        결과 이벤트는 재전송되지 않으므로, 이 신호 뒤에는 대기 중인 결과가 안 올 수 있다.
        """
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        if self._ws is None or self._ws.closed:
            future.set_result(None)
            return future
        self._connection_lost_waiters.add(future)
        future.add_done_callback(self._connection_lost_waiters.discard)
        return future

    def _fire_connection_lost(self) -> None:
        for future in list(self._connection_lost_waiters):
            if not future.done():
                future.set_result(None)

    async def request_transfer(self, call_id: str, transfer_params: dict) -> dict:
        """콜 전환을 요청하고 완료/실패 응답을 기다린다.

        `transfer_params['timeout']` 은 **대상 응답 대기**(서버가 관리)다. 여기서 기다리는 것은
        전환 통화가 끝날 때까지이며, 브리지 길이와 무관한 방어선
        (`TRANSFER_RESULT_MAX_WAIT_S`)만 둔다 — 서버가 결과 이벤트를 반드시 보낸다는 계약을
        신뢰하기 때문이다. 통화가 먼저 끝나면 종료 통지가 이 대기를 정리한다.

        연결이 없으면 대기를 만들기 전에 ``AgentConnectionError`` 를 던진다. 끊긴 뒤 다시 붙기까지
        (1초~) `_ws` 는 None 이고, 닫히는 중에는 객체만 남아 있다 — 이 객체는 재연결 내내 살아
        있으므로 호출부의 `_control_ws is None` 검사로는 이 창을 막을 수 없다. 기다리지 않는 이유는
        전환은 지금 통화 중인 고객을 옮기는 일이라, 언제 붙을지 모르는 연결에 묶어 두면 안 되기 때문이다.
        """
        ws = self._ws
        if ws is None or ws.closed:
            raise AgentConnectionError(TRANSFER_NO_CONNECTION_MESSAGE)
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_transfers[request_id] = future
        self._pending_by_call.setdefault(call_id, set()).add(request_id)
        try:
            try:
                await ws.send_str(
                    json.dumps(
                        {
                            "event": "call.transfer",
                            "callId": call_id,
                            # 서버는 이 값을 해석하지 않고 결과 이벤트에 그대로 되돌린다.
                            "transfer": {**transfer_params, "requestId": request_id},
                        }
                    )
                )
            except (aiohttp.ClientError, ConnectionError) as e:
                # 위 검사와 전송 사이에 끊겼다. 서버는 이 요청을 받지 못했다.
                raise AgentConnectionError(TRANSFER_NO_CONNECTION_MESSAGE) from e
            return await asyncio.wait_for(future, timeout=TRANSFER_RESULT_MAX_WAIT_S)
        except asyncio.TimeoutError:
            log.error(
                f"전환 결과가 {TRANSFER_RESULT_MAX_WAIT_S:.0f}초 안에 오지 않았다: "
                f"call={call_id} request={request_id} — 서버가 결과 이벤트를 보내지 않았다는 뜻이다"
            )
            raise
        finally:
            # 어떤 경로로 끝나든 상관을 놓는다. 예전에는 타임아웃 시 dict 에 항목이 남아,
            # 뒤늦게 도착한 결과가 취소된 future 를 건드려 연결을 죽였다.
            self._discard_pending(call_id, request_id)

    def _discard_pending(self, call_id: str | None, request_id: str) -> None:
        self._pending_transfers.pop(request_id, None)
        if call_id and call_id in self._pending_by_call:
            self._pending_by_call[call_id].discard(request_id)
            if not self._pending_by_call[call_id]:
                del self._pending_by_call[call_id]

    def _resolve_request_id(self, data: dict) -> str | None:
        """이벤트가 어느 요청의 응답인지 판정한다.

        서버가 echo 한 `requestId` 가 정답이다. 없으면 callId 로 폴백한다 — requestId 를
        되돌리지 않는 **구 서버**와 섞이는 전환 기간용이다. 그 기간에는 같은 통화의 다중 전환을
        구분할 수 없다(대기가 하나뿐일 때만 매핑한다). 서버 배포가 끝나면 이 폴백은 제거 대상이다.
        """
        request_id = data.get("requestId")
        if request_id:
            return request_id if request_id in self._pending_transfers else None
        call_id = data.get("callId")
        waiting = self._pending_by_call.get(call_id or "")
        if waiting and len(waiting) == 1:
            return next(iter(waiting))
        return None

    def _on_transfer_event(self, data: dict) -> None:
        """전환 이벤트로 대기 중인 Future 를 resolve 한다."""
        if data.get("event") not in ("call.transfer.completed", "call.transfer.failed"):
            return  # started/connected 는 진행 알림 — 대기를 깨우지 않는다
        request_id = self._resolve_request_id(data)
        if not request_id:
            return
        future = self._pending_transfers.get(request_id)
        # done() 가드: 이미 취소/완료된 대기에 set_result 를 하면 InvalidStateError 가 나고,
        # 그 예외가 수신 루프를 죽였다(2026-08-12). 중복 도착도 여기서 조용히 흡수된다.
        if future is not None and not future.done():
            future.set_result(data.get("transfer", {}))
        self._discard_pending(data.get("callId"), request_id)

    def _schedule_transfer_cleanup(self, call_id: str | None) -> None:
        """통화 종료 후 유예를 두고 그 통화의 전환 대기를 정리한다."""
        if not call_id or call_id not in self._pending_by_call:
            return
        request_ids = list(self._pending_by_call[call_id])

        def _cleanup() -> None:
            for rid in request_ids:
                future = self._pending_transfers.get(rid)
                if future is not None and not future.done():
                    # 통화가 끝났는데 결과가 오지 않았다 — 대기를 놓아 호출부가 매달리지 않게 한다.
                    log.warning(f"통화 종료 후 전환 결과 미도착 — 대기 정리: call={call_id} request={rid}")
                    future.cancel()
                self._discard_pending(call_id, rid)

        loop = asyncio.get_event_loop()
        timer = loop.call_later(TRANSFER_LATE_ARRIVAL_GRACE_S, _cleanup)
        self._cleanup_timers.add(timer)

    async def send(self, data: dict[str, Any]) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps(data))

    async def close(self) -> None:
        self._running = False
        self._fire_connection_lost()
        for timer in self._cleanup_timers:
            timer.cancel()
        self._cleanup_timers.clear()
        for fut in self._pending_transfers.values():
            if not fut.done():
                fut.cancel()
        self._pending_transfers.clear()
        self._pending_by_call.clear()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()
