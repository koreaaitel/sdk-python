"""수신 통화 선별(``@agent.on_incoming``) — 받기 전에 거절할 기회.

서버가 ``call.incoming`` 을 보내면 SDK 는 지금까지 무조건 ``call.accept`` 로 답했다. 훅을
등록하면 그 사이에 훅이 돌고, 훅이 ``reject()`` 를 부르면 ``call.reject`` 로 답한다.
서버는 사유가 있는 거절을 받으면 **응답하기 전에** 통화를 끝낸다(과금 없음, 다른 경로로
넘기지 않음). 사유 없는 거절은 서버가 다른 경로로 넘기므로 SDK 는 언제나 사유를 싣는다.

``IncomingCall`` 은 ``CallSession`` 이 **아니다**. 세션은 수락한 뒤에야 만들어진다 — 거절한
통화에는 세션도, 미디어도, ``session_setup`` 도 없다.

훅이 ``transfer(to)`` 를 부르면 AI 없이 받아서 곧바로 ``to`` 로 넘긴다. AI 세션은 열지 않는다
(첫 인사도, 모델 연결도 없다). 서버는 미디어가 붙은 뒤에야 통화를 받으므로 SDK 가 미디어만
붙이고, 발신자 음성이 흐르기 시작하면(=서버가 받았다) 전환을 요청한다.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any, Literal

from ._session import build_transfer_payload

log = logging.getLogger("clawops.agent")

RejectReason = Literal["rejected", "busy"]
"""``"rejected"`` = SIP 403(기본), ``"busy"`` = SIP 486."""

REJECT_REASONS: tuple[str, ...] = ("rejected", "busy")

# 훅이 결정을 내려야 하는 상한(초). 서버는 응답을 11초 기다린다 — 여유를 넉넉히 남긴다.
# 이 시간을 넘기거나 훅이 예외를 던지면 수락한다(fail-open): 선별 코드의 결함이 통화를
# 통째로 막아서는 안 된다.
INCOMING_HOOK_DEADLINE_S = 5.0


class _Done:
    """이미 끝난 awaitable. ``reject()`` 가 동기 훅에서도 효과를 내게 하는 장치다.

    ``async def reject`` 였다면 동기 훅의 ``call.reject()`` 는 코루틴만 만들고 아무 일도 하지
    않는다. 결정은 호출 시점에 즉시 기록하고, ``await`` 는 형식으로만 받는다.
    """

    __slots__ = ()

    def __await__(self) -> Generator[Any, None, None]:
        return
        yield  # pragma: no cover — 제너레이터로 만들기 위한 자리


class IncomingCall:
    """받기 전의 수신 통화. ``@agent.on_incoming`` 훅의 인자다.

    Attributes:
        call_id: 통화 ID.
        from_number: 발신 번호. 번호 표시제한 통화도 번호는 그대로 온다.
        to_number: 이 에이전트의 번호(수신 번호).
        caller_id_restricted: 발신자가 번호 표시제한을 걸고 건 통화면 ``True``.
        forwarded_from: 착신전환으로 들어온 통화에서 발신자가 원래 건 번호. 착신전환을 실행한
            통신사가 전환 정보를 함께 보내온 경우에만 값이 있고, 직접 걸려온 통화는 ``None``.
    """

    __slots__ = (
        "_closed",
        "_on_decide",
        "_reason",
        "_transfer",
        "call_id",
        "caller_id_restricted",
        "forwarded_from",
        "from_number",
        "to_number",
    )

    def __init__(
        self,
        *,
        call_id: str,
        from_number: str,
        to_number: str,
        caller_id_restricted: bool = False,
        forwarded_from: str | None = None,
    ) -> None:
        self.call_id = call_id
        self.from_number = from_number
        self.to_number = to_number
        self.caller_id_restricted = caller_id_restricted
        self.forwarded_from = forwarded_from
        self._reason: str | None = None
        # transfer() 로 정한 전환 요청 본문. reject 와 둘 중 먼저 정한 것만 남는다.
        self._transfer: dict[str, Any] | None = None
        # 결정 시한이 지났다 — 이미 수락(또는 거절)을 보냈으므로 더는 바꿀 수 없다.
        self._closed = False
        # 결정 즉시 에이전트를 깨우는 콜백(훅이 끝나기를 기다리지 않는다).
        self._on_decide: Any = None

    @property
    def rejected(self) -> bool:
        """``reject()`` 가 시한 안에 불렸는가."""
        return self._reason is not None

    @property
    def reject_reason(self) -> RejectReason | None:
        return self._reason  # type: ignore[return-value]

    @property
    def transfer_request(self) -> dict[str, Any] | None:
        """``transfer()`` 가 시한 안에 불렸으면 서버에 보낼 전환 요청 본문, 아니면 ``None``."""
        return self._transfer

    def _decided(self) -> bool:
        return self._reason is not None or self._transfer is not None

    def transfer(
        self,
        to: str,
        *,
        destination_type: Literal["pstn", "sip"] = "pstn",
        caller_id: str | None = None,
        caller_id_mode: Literal["account", "original"] | None = None,
        timeout: int = 30,
        hold_media: str = "ringback",
        whisper: str | None = None,
        context: dict | None = None,
        failure_message: str | None = None,
        failure_voice: str | None = None,
    ) -> _Done:
        """AI 없이 받아서 곧바로 ``to`` 로 넘긴다. ``await call.transfer("0212345678")`` 로 부른다.

        - AI 세션을 열지 않는다. 발신자는 AI 인사 없이 연결음을 듣는다.
        - 이 통화에는 ``call_start`` 등 통화 이벤트가 없다(AI 세션이 없으므로).
        - 인자는 ``CallSession.transfer`` 와 같다. 넘긴 뒤 AI 가 이어받을 수 없으므로
          ``mode="blind"``·``after_transfer="terminate"`` 로 고정한다. 대상이 받지 않으면
          ``failure_message`` 를 들려주고 끊는다(없으면 그냥 끊는다).
        - ``reject()`` 와 둘 중 먼저 부른 것만 효과가 있다. 결정 시한(5초)이 지나면 무시된다.
        """
        payload = build_transfer_payload(
            to,
            destination_type=destination_type,
            mode="blind",
            after_transfer="terminate",
            hold_media=hold_media,
            whisper=whisper,
            context=context,
            caller_id=caller_id,
            caller_id_mode=caller_id_mode,
            timeout=timeout,
            failure_message=failure_message,
            failure_voice=failure_voice,
        )
        if self._closed:
            log.warning(
                "IncomingCall.transfer() — 결정 시한이 지나 이미 처리된 통화입니다(무시): %s",
                self.call_id,
            )
            return _Done()
        if self._decided():
            return _Done()
        self._transfer = payload
        if self._on_decide is not None:
            self._on_decide()
        return _Done()

    def reject(self, reason: RejectReason = "rejected") -> _Done:
        """이 통화를 받지 않고 거절한다. ``await call.reject()`` 로 부른다.

        Args:
            reason: ``"rejected"``(기본, SIP 403) 또는 ``"busy"``(SIP 486, 통화중).

        두 번 불러도 첫 결정이 유지된다. 결정 시한(5초)이 지난 뒤에 부르면 이미 수락한
        통화라 아무 일도 하지 않고 경고만 남긴다.
        """
        if reason not in REJECT_REASONS:
            raise ValueError(f"reason={reason!r} — 'rejected' 또는 'busy' 만 됩니다")
        if self._closed:
            log.warning(
                "IncomingCall.reject() — 결정 시한이 지나 이미 처리된 통화입니다(무시): %s",
                self.call_id,
            )
            return _Done()
        if self._decided():
            return _Done()
        self._reason = reason
        if self._on_decide is not None:
            self._on_decide()
        return _Done()

    def __repr__(self) -> str:
        return (
            f"IncomingCall(call_id={self.call_id!r}, from_number={self.from_number!r}, "
            f"to_number={self.to_number!r}, caller_id_restricted={self.caller_id_restricted!r}, "
            f"forwarded_from={self.forwarded_from!r})"
        )
