"""CallSession: per-call 상태 관리."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Callable, Awaitable, Literal

from clawops.agent._telemetry import CallMetrics

log = logging.getLogger("clawops.agent")


class DtmfCollectorBusy(RuntimeError):
    """``collect_dtmf`` 가 이미 돌고 있는데 또 불렸다.

    실시간 모델은 도구를 **중복으로 호출한다** — 앞 호출이 아직 자리를 기다리는 동안 같은
    도구를 한 번 더 낸다(2026-09-03 실통화에서 관측). 이걸 맨 ``RuntimeError`` 로 던지면
    도구 래퍼가 ``"Error: ..."`` 로 감싸 모델에게 돌려주고, 모델은 뭔가 고장난 줄 알고
    **그 뒤로 도구를 다시 부르지 않는다.** 그러면 발신자가 누르는 키는 아무도 받지 않는다.

    ``RuntimeError`` 를 상속하므로 기존에 이걸 잡던 코드는 그대로 돈다. 도구 경로만
    이 타입을 따로 잡아 "고장"이 아니라 "기다리라"는 뜻으로 옮긴다.
    """


def build_transfer_payload(
    to: str,
    *,
    destination_type: Literal["pstn", "sip"] = "pstn",
    mode: str = "blind",
    after_transfer: str = "terminate",
    hold_media: str = "ringback",
    whisper: str | None = None,
    context: dict | None = None,
    caller_id: str | None = None,
    caller_id_mode: Literal["account", "original"] | None = None,
    timeout: int = 30,
    failure_message: str | None = None,
    failure_voice: str | None = None,
) -> dict[str, Any]:
    """``call.transfer`` 요청 본문을 만든다. ``CallSession.transfer`` 와 ``IncomingCall.transfer`` 가 함께 쓴다."""
    # 서버는 'original' 만 특별 취급하고 나머지 값은 조용히 무시한다. 오타를 그대로
    # 흘려보내면 아무 에러 없이 계정 번호가 나가고, 개발자는 켰다고 믿는다 — 여기서 막는다.
    if caller_id_mode is not None and caller_id_mode not in ("account", "original"):
        raise ValueError(f"caller_id_mode must be 'account' or 'original', got {caller_id_mode!r}")
    payload: dict[str, Any] = {
        "to": to,
        "destinationType": destination_type,
        "mode": mode,
        "afterTransfer": after_transfer,
        "holdMedia": hold_media,
        "whisper": whisper,
        "context": context,
        "callerId": caller_id,
        "timeout": timeout,
    }
    # 안 주면 키를 붙이지 않는다 — 구 서버와 기존 동작을 그대로 둔다(additive).
    if caller_id_mode is not None:
        payload["callerIdMode"] = caller_id_mode
    # 같은 이유로 안 주면 키 자체를 안 붙인다. 빈 문자열도 보내지 않는다 — 서버도 막지만
    # 빈 문장을 합성하러 왕복시킬 이유가 없다.
    if failure_message:
        payload["failureMessage"] = failure_message
    if failure_voice:
        payload["failureVoice"] = failure_voice
    return payload


class CallSession:
    def __init__(
        self,
        *,
        call_id: str,
        from_number: str,
        to_number: str,
        account_id: str,
        direction: str = "inbound",
        caller_id_restricted: bool = False,
        forwarded_from: str | None = None,
    ) -> None:
        self.call_id = call_id
        self.from_number = from_number
        self.to_number = to_number
        self.account_id = account_id
        self.direction = direction
        # 수신 통화에서 발신자가 번호 표시제한(`*23#` 등)을 걸고 건 통화면 True. 번호는
        # from_number 에 그대로 있고 여부만 여기서 알 수 있다. 통신사가 표시제한 여부를 보내지
        # 않은 통화와 발신 통화는 False.
        self.caller_id_restricted = caller_id_restricted
        # 착신전환으로 들어온 수신 통화에서 발신자가 원래 건 번호(clawops#1423). 착신전환을 실행한
        # 통신사가 전환 정보를 함께 보내온 경우에만 값이 있고, 직접 걸려온 통화와 발신 통화는 None.
        self.forwarded_from = forwarded_from
        self.status: str = "queued" if direction == "outbound" else "ringing"
        # 서버가 통보한 최종 종료 상태. 통화가 끝나기 전에는 None.
        # completed(성사) / no-answer / busy / rejected / canceled / failed.
        self.ended_status: str | None = None
        # 서버가 확정한 통화 시간(초). 통화가 끝나기 전에는 None.
        # ⚠️ `duration` 과 다르다 — 그쪽은 SDK 가 로컬 시계로 재는 **경과 시간**이라 통화 중에도
        # 읽히고, 세션이 붙기 전후의 오차를 포함한다. 기록·정산에 쓸 값은 이쪽이다.
        self.ended_duration: int | None = None
        self.start_time = datetime.now()
        self.metadata: dict[str, Any] = {}

        self._metrics = CallMetrics(_start_time_ms=time.time() * 1000)

        self._send_audio_fn: Callable[[bytes], Awaitable[None]] | None = None
        self._send_clear_fn: Callable[[], Awaitable[None]] | None = None
        self._hangup_fn: Callable[[], Awaitable[None]] | None = None

        self._event_handlers: dict[str, list[Callable[..., Awaitable[None]]]] = {}
        self._ended_event = asyncio.Event()

        # Transfer
        self._transfer_fn: Callable[[dict], Awaitable[dict]] | None = None
        # control 연결이 끊기면 완료되는 future 를 만든다. 전환 결과를 기다리는 도구가 쓴다.
        self._transfer_connection_lost_fn: Callable[[], Any] | None = None

        # DTMF
        self._send_dtmf_fn: Callable[[str], Awaitable[None]] | None = None
        self._media_ws: Any | None = None  # is_connected 체크용
        self._dtmf_collector_active: bool = False
        self._dtmf_queue: asyncio.Queue[str] = asyncio.Queue()
        # 패시브 DTMF(=collect_dtmf 가 안 걸린 입력)의 debounce 버퍼. 통화별로 들고 있어야
        # 동시 통화의 키패드가 서로 섞이지 않는다. flush 는 ClawOpsAgent 가 돌린다 —
        # debounce 값이 에이전트 설정이라서다.
        self._passive_dtmf_buffer: list[str] = []
        self._passive_dtmf_task: asyncio.Task[None] | None = None
        # 버퍼에 남은 키를 대기 없이 확정하는 함수. ClawOpsAgent 가 버퍼에 키를 넣을 때 건다 —
        # collect_dtmf 가 열릴 때 부른다.
        self._passive_dtmf_confirm_now: Callable[[], None] | None = None
        # 한 통화의 자동 전달은 차례로 보낸다(OpenAI 는 주입이 두 번의 호출이라 겹치면 응답이 둘 생긴다).
        self._passive_dtmf_feed_lock: asyncio.Lock | None = None

    def bind_transport(
        self,
        *,
        send_audio: Callable[[bytes], Awaitable[None]],
        send_clear: Callable[[], Awaitable[None]],
        hangup: Callable[[], Awaitable[None]],
        send_dtmf: Callable[[str], Awaitable[None]] | None = None,
        media_ws: Any | None = None,
        transfer: Callable[[dict], Awaitable[dict]] | None = None,
    ) -> None:
        """Wire the audio/DTMF/hangup transport for this call.

        Public composition point for servers that drive a call from an already-open
        media transport (e.g. a mediaUrl-dispatched worker) instead of going through
        ClawOpsAgent's control WS. Encapsulates the transport-binding fields so callers
        never assign the private ``_send_audio_fn`` / ``_media_ws`` / ... attributes.
        ``transfer`` is optional and only needed when the caller can service transfers.
        """
        self._send_audio_fn = send_audio
        self._send_clear_fn = send_clear
        self._hangup_fn = hangup
        self._send_dtmf_fn = send_dtmf
        self._media_ws = media_ws
        self._transfer_fn = transfer

    @property
    def metrics(self) -> CallMetrics:
        return self._metrics

    @property
    def duration(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()

    async def send_audio(self, audio: bytes) -> None:
        """선택된 transport 형식의 오디오를 상대방에게 보낸다.

        기본 ClawOpsAgent 연결은 G.711 μ-law(8kHz, mono)이다. 여기에 PCM16을
        보내면 잡음이 된다. 서버와 명시적으로 PCM 프로필을 검증한 내부 연결에서만
        해당 PCM 바이트를 보낼 수 있다. 이 메서드는 변환이나 형식 협상을 하지 않는다.
        """
        if self._send_audio_fn:
            await self._send_audio_fn(audio)
            self._metrics.record_first_response()

    async def clear_audio(self) -> None:
        if self._send_clear_fn:
            await self._send_clear_fn()
            self._metrics.record_barge_in()

    async def hangup(self) -> None:
        if self._hangup_fn:
            await self._hangup_fn()

    def on(self, event: str, handler: Callable[..., Awaitable[None]]) -> None:
        self._event_handlers.setdefault(event, []).append(handler)

    async def wait(self) -> None:
        """통화가 종료될 때까지 대기한다."""
        await self._ended_event.wait()

    def _mark_ended(self, status: str | None = None) -> None:
        """통화 종료를 알린다. (내부 전용)

        Args:
            status: 서버가 통보한 최종 상태(completed/no-answer/busy/rejected/
                canceled/failed). 주어지면 그대로 확정한다 — 상대가 받지 않은 통화를
                'completed' 로 뭉개지 않기 위함이다. 생략하면 미디어 세션 정리 경로에서의
                호출이므로, 아직 종료 전일 때만 'completed' 로 채우고 이미 확정된 서버
                상태는 덮어쓰지 않는다.
        """
        if status is not None:
            self.status = status
            self.ended_status = status
        elif not self._ended_event.is_set():
            self.status = "completed"
            self.ended_status = "completed"
        # 아직 안 깨어난 패시브 DTMF flush 를 무효화한다. 취소하지 않는 이유는
        # _flush_passive_dtmf 주석 참고 — 이미 주입 중인 flush 를 자르지 않기 위해서다.
        self._passive_dtmf_task = None
        self._passive_dtmf_buffer.clear()
        self._ended_event.set()

    async def _emit(self, event: str, *args: Any) -> None:
        for handler in self._event_handlers.get(event, []):
            await handler(self, *args)

    def _route_dtmf(self, digit: str) -> None:
        """DTMF digit을 수집 큐에 넣는다 (내부 전용).

        ``ClawOpsAgent`` 는 수집이 열려 있을 때만 부른다. 구간 밖 키는 모델에 자동 전달되므로
        여기까지 오면 같은 키가 두 경로로 간다 — 동의 때 누른 ``1`` 이 다음 수집의 첫 자리가
        돼 「1534」 가 나왔다."""
        self._dtmf_queue.put_nowait(digit)

    async def _wait_prompt_played(self, limit: float) -> None:
        """지금까지 보낸 안내 음성이 발신자에게 다 재생될 때까지 기다린다.

        재생 끝을 알 수단(media WS 의 mark)이 없는 transport 면 바로 돌아온다 — 종전 동작이다.
        """
        wait_played_out = getattr(self._media_ws, "wait_played_out", None)
        if wait_played_out is None:
            return
        t0 = time.monotonic()
        try:
            played = await wait_played_out(limit)
        except Exception as e:  # noqa: BLE001 — 끊기는 소켓에서 mark 전송이 실패할 수 있다. 그때부터 센다.
            log.info("키패드 수집: 안내 재생 끝 확인 실패 (%s)", type(e).__name__)
            return
        log.info(
            "키패드 수집: 안내 재생 끝 %.2fs%s", time.monotonic() - t0, "" if played else " (확인 못 함)"
        )

    async def collect_dtmf(
        self,
        max_digits: int,
        finish_on_key: str = "#",
        timeout: float = 5,
        secure: bool = False,
        max_wait: float | None = 30,
    ) -> str:
        """DTMF 입력을 수집한다.

        ``timeout`` 은 **자리 사이** 대기다(inter-digit). ``<Gather>`` 를 비롯한 IVR 관행과
        같은 의미이고, 자리가 들어올 때마다 다시 시작한다.

        대기는 **안내 음성이 다 재생된 뒤**부터 센다(Twilio ``<Gather>`` 와 같다). 모델은 안내
        텍스트를 다 만든 순간 이 도구를 부르지만 발신자 쪽에선 음성이 아직 10초 넘게 남아 있을
        수 있다 — 그때부터 세면 누를 틈도 없이 빈 값으로 끝난다. 재생 중에 누른 첫 키는 그대로
        받고(안내는 ``ClawOpsAgent`` 가 끊는다), 그 순간부터 센다.

        수집이 열린 **뒤에** 누른 키만 받는다. 그 전에 누른 키는 모델에 자동 전달된다.

        ``max_wait`` 는 그 관행이 만드는 최악을 막는 **전체 상한**이다. 자리마다 리셋되는
        타이머만 있으면 수집 하나가 최대 ``max_digits × timeout`` 동안 산다 — 11자리·5초면
        **55초**이고, 그동안 모델은 이 도구에 붙들려 아무 말도 못 한다. 발신자에게는 그냥
        먹통이다. 이것도 재생이 끝난 뒤부터 센다. ``None`` 을 주면 상한 없이 종전대로 돈다.

        ⚠️ **수집값을 로그에 쓰지 않는다.** 키패드로 받는 값은 카드번호·주민번호일 수 있고,
        실제로 그런 구성이 있다. ``secure`` 와 무관하게 **자릿수만** 남긴다 — 이 인자는
        호환을 위해 남아 있을 뿐 로깅을 좌우하지 않는다.
        """
        if self._dtmf_collector_active:
            raise DtmfCollectorBusy("이미 DTMF 수집 중입니다")

        self._dtmf_collector_active = True
        # 수집 직전에 눌러 아직 자동 전달 대기 중인 키는 지금 모델에 넘긴다. 수집으로 가져오면
        # 앞 질문의 답(동의 1 등)이 섞이고, 버리면 잃는다. 2초 대기 동안 기다리게 두면 도구 호출이
        # 걸린 채로 늦게 들어간다.
        if self._passive_dtmf_buffer and self._passive_dtmf_confirm_now is not None:
            self._passive_dtmf_confirm_now()
        # 구간 밖 키가 남아 있으면 버린다(방어 — 정상 경로에선 비어 있다).
        while not self._dtmf_queue.empty():
            self._dtmf_queue.get_nowait()
        collected: list[str] = []
        loop = asyncio.get_running_loop()

        try:
            # 1) 안내가 다 재생되거나 첫 키가 올 때까지. 재생 대기 상한은 max_wait(없으면 60초).
            first = asyncio.ensure_future(self._dtmf_queue.get())
            played = asyncio.ensure_future(self._wait_prompt_played(max_wait if max_wait is not None else 60))
            try:
                await asyncio.wait({first, played}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                played.cancel()
                if not first.done():
                    # 아직 깨어나지 않은 get() 은 취소해도 키를 잃지 않는다.
                    first.cancel()
            pending: list[str] = [first.result()] if first.done() and not first.cancelled() else []

            # 2) 여기서부터 자리 사이 대기·전체 상한을 센다.
            deadline = loop.time() + max_wait if max_wait is not None else None
            done = False
            for digit in pending:
                if digit == finish_on_key:
                    done = True
                    break
                collected.append(digit)
            while not done and len(collected) < max_digits:
                wait_s = timeout
                if deadline is not None:
                    # 남은 예산이 자리 간 대기보다 짧으면 그만큼만 기다린다. 0 이하면
                    # 전체 상한에 닿은 것이라 지금까지 모인 값으로 확정한다.
                    wait_s = min(timeout, deadline - loop.time())
                    if wait_s <= 0:
                        break
                try:
                    digit = await asyncio.wait_for(self._dtmf_queue.get(), timeout=wait_s)
                    if digit == finish_on_key:
                        break
                    collected.append(digit)
                except asyncio.TimeoutError:
                    while not self._dtmf_queue.empty() and len(collected) < max_digits:
                        d = self._dtmf_queue.get_nowait()
                        if d == finish_on_key:
                            break
                        collected.append(d)
                    break
        finally:
            self._dtmf_collector_active = False
            # Drain remaining queue to prevent stale digits in next collect
            while not self._dtmf_queue.empty():
                self._dtmf_queue.get_nowait()

        result = "".join(collected)
        log.info("DTMF collected: %d digits", len(result))
        return result

    async def transfer(
        self,
        to: str,
        *,
        destination_type: Literal["pstn", "sip"] = "pstn",
        mode: str = "blind",
        after_transfer: str = "terminate",
        hold_media: str = "ringback",
        whisper: str | None = None,
        context: dict | None = None,
        caller_id: str | None = None,
        caller_id_mode: Literal["account", "original"] | None = None,
        timeout: int = 30,
        failure_message: str | None = None,
        failure_voice: str | None = None,
    ) -> dict:
        """Transfer the current call to a phone number or SIP endpoint.

        destination_type='pstn' (default): ``to`` is a phone number dialed via carrier.
        destination_type='sip': ``to`` is a SIP URI (e.g. ``sip:user@host``) connected
        directly to a SIP endpoint without going through the PSTN carrier. Requires the
        account to have an active ``sip_trunk`` add-on; otherwise the transfer fails and
        the call continues with the AI (result ``{"status": "failed", ...}``).

        전환받는 쪽에 표시되는 발신번호는 기본이 **계정 보유번호**(인바운드면 착신 070)다.

        ``caller_id_mode="original"`` 은 인바운드 통화의 **발신자 번호를 표시하려는 선호**다.
        표시할 수 없는 통화(KCT 직결 인입이 아니거나 국내 번호로 정규화되지 않는 발신번호)면
        조용히 계정 보유번호로 내려앉고 **전환은 그대로 성사된다**. 바뀌는 것은 받는 쪽 표시뿐이라
        (통화 자체는 계정 번호에서 넘기는 전환이다) 받는 장비에 따라 계정 번호가 보일 수 있다.

        ``caller_id`` 는 번호를 직접 주는 **지시**라 성격이 다르다. 허용 범위(계정 보유번호
        또는 KCT 직결 인입 통화의 발신자)를 벗어나면 전환 자체가 실패한다. 둘 다 주면
        ``caller_id`` 가 이기고 ``caller_id_mode`` 는 무시된다 — 우선순위 판단은 서버가 한다.

        ``failure_message`` 는 전환이 연결되지 않았을 때(무응답·통화중·대상 실패) **발신자에게**
        들려주고 끊을 문장이다. 주지 않으면 종전대로 아무 말 없이 끊긴다. ``whisper`` 와 듣는
        사람이 반대다 — 저건 전화를 받은 담당자에게만 들린다.

        ⚠️ ``after_transfer="return"`` 에서는 재생되지 않는다. 그 모드는 AI 가 통화를 이어받으므로
        무엇을 말할지는 당신 코드가 정한다. 다만 이어받을 채널이 사라져 서버가 ``terminate`` 로
        내려앉힌 통화에서는 재생된다 — 그게 발신자가 무음에 남는 경우다.
        """
        if not self._transfer_fn:
            raise RuntimeError("transfer not available")
        payload = build_transfer_payload(
            to,
            destination_type=destination_type,
            mode=mode,
            after_transfer=after_transfer,
            hold_media=hold_media,
            whisper=whisper,
            context=context,
            caller_id=caller_id,
            caller_id_mode=caller_id_mode,
            timeout=timeout,
            failure_message=failure_message,
            failure_voice=failure_voice,
        )
        return await self._transfer_fn(payload)

    async def send_dtmf_sequence(self, digits: str) -> None:
        """여러 DTMF digit을 순서대로 전송한다."""
        if not self._send_dtmf_fn:
            raise RuntimeError("DTMF 전송 함수가 바인딩되지 않았습니다")
        for ch in digits:
            if self._media_ws and not self._media_ws.is_connected:
                raise ConnectionError("DTMF 전송 중 연결이 끊어졌습니다")
            if ch == "w":
                await asyncio.sleep(0.5)
            elif ch == "W":
                await asyncio.sleep(1.0)
            elif ch in "0123456789*#":
                await self._send_dtmf_fn(ch)
            else:
                raise ValueError(f"유효하지 않은 DTMF 문자: {ch}")
