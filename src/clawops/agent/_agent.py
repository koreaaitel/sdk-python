"""ClawOpsAgent: 메인 진입점.

Control WS 연결, per-call Media WS 생성, Session 관리를 조합한다.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import signal
import time
import inspect
from typing import Any, Awaitable, Callable, Literal, TypedDict, TypeVar, Union

from .._exceptions import AgentAuthRejectedError, AgentConnectionError, AgentError
from ._builtin_tools import BuiltinTool, resolve_builtin_tools
from ._control_ws import CLOSE_REPLACED, ControlWebSocket
from ._health import start_health_server
from ._deploy_checks import (
    clear_stale_ready_marker,
    remove_ready_marker,
    take_stale_clear_notice,
    warn_if_signals_blocked,
    write_ready_marker,
)
from ._hold_audio import load_hold_audio
from ._incoming import INCOMING_HOOK_DEADLINE_S, IncomingCall
from ._audio import apply_ulaw_gain, ulaw_to_pcm16
from .mcp._client import MCPClient
from ._media_ws import MediaWebSocket
from ._session import CallSession
from ._recorder import AudioRecorder
from ._telemetry import get_sdk_info
from ._tool import ToolRegistry
from .pipeline import Session
from .tracing import TracingConfig
from .tracing._spans import call_span, mcp_connect_span, setup_tracing

log = logging.getLogger("clawops.agent")

# 미디어 정리 후 서버 종료 프레임을 기다리는 상한(초). 정상 경로에서는 밀리초 안에 풀리고,
# 제어 연결이 죽어 프레임이 아예 안 올 때만 이 값을 다 쓴다.
#
# 🔴 이건 **임시 장치**다. 서버가 미디어 WS 를 먼저 닫고 정리를 마친 뒤에 종료 프레임을
#    보내기 때문에 클라이언트가 그 순서를 보정하고 있다. 서버가 종료 프레임을 미디어 WS
#    닫기 **전에** 보내게 되면 이 대기는 통째로 필요 없어진다. 다만 그때도 구 서버가 남아
#    있는 동안은 유지해야 한다 — 버전 협상이 없어 "모두 새 서버" 를 확신할 방법이 없다.
TERMINAL_FRAME_GRACE_S = 2.0

# IncomingCall.transfer() — AI 없이 받아 곧바로 넘기는 통화.
# 서버는 미디어 WS 가 붙은 뒤 통화를 받는다. 받은 뒤에야 발신자 음성이 흐르므로 첫 프레임을 신호로 쓴다.
TRANSFER_ONLY_ANSWER_WAIT_S = 10.0
# 받기와 전환 가능 상태 등록 사이의 짧은 틈에 걸린 거절(NO_SESSION)을 다시 시도하는 기간·간격.
TRANSFER_ONLY_RETRY_S = 5.0
TRANSFER_ONLY_RETRY_INTERVAL_S = 0.2
# 서버의 NO_SESSION 거절 사유(call-engine transfer-handler `_assertMediaSession`).
NO_MEDIA_SESSION_REASON = "No active media session"

# drain() 이 진행 중 통화를 기다리는 기본 상한(초). **기본은 무한이다.**
#
# 예전에는 120초였다. 그 값은 ECS `stopTimeout` 상한(120초)에서 나온 것인데 k8s 에도 그대로
# 쓰였다 — k8s 의 `terminationGracePeriodSeconds` 에는 상한이 없다. 실측하면 진행 중이던 통화의
# **29.8%(464/1,559)가 110초를 넘는다.** 기다렸으면 끝났을 통화를 우리가 자르고 있었다는 뜻이다.
#
# 이제 끊는 주체는 **플랫폼 SIGKILL 하나**다. 유예가 짧은 환경(ECS)은 그 유예가 곧 상한이므로
# 여기서 따로 셀 이유가 없고, 유예가 긴 환경(k8s)은 통화가 끝날 때까지 기다리는 것이 맞다.
# 유예보다 먼저 끊고 싶으면 호출측이 명시적으로 값을 준다.
DEFAULT_DRAIN_TIMEOUT_S = math.inf
# drain() 이 마지막 통화가 끝났는지 다시 보는 주기(초).
DRAIN_POLL_INTERVAL_S = 0.2

# SIGTERM 뒤 **자리를 계속 들고 있으면서** 후임의 인계를 기다리는 상한(초).
#
# 이 대기가 배포의 빈틈을 없앤다. 예전에는 SIGTERM 을 받는 즉시 자리를 놓았는데, 그때
# 후임이 아직 안 붙었으면 그 사이 오는 전화가 전부 죽었다(readinessProbe 는 그 순간이
# 오지 않게 오케스트레이터를 붙잡아 두는 우회다).
#
# ⚠ 짧아야 한다. **후임이 아예 없을 때는 이 대기가 손해다** — 스케일인이나 단순 정지에서는
#   예전처럼 자리를 즉시 놓는 편이 낫다(그 뒤 전화는 "안 걸림"). 여기서 받은 전화가 유예
#   만료에 "통화 중 절단"이 되면 그게 더 나쁘다.
DEFAULT_HANDOVER_WAIT_S = 20.0

# SIGTERM 부터 세는 **절대 마감**(초). 인계 대기와 드레이닝이 이 하나를 나눠 쓴다.
# **기본은 무한이다** — 이유는 DEFAULT_DRAIN_TIMEOUT_S 주석과 같다.
#
# 예전에는 110초였다(ECS stopTimeout 120초 − 정리 여유 10초). 그 값이 k8s 에도 그대로 쓰이면서,
# 유예가 넉넉한 환경에서도 110초에 통화를 잘랐다.
#
# 마감이 유한할 때의 규약은 그대로다: 두 국면이 이 하나를 **나눠 쓴다**(더하지 않는다).
# 인계가 2초에 끝나면 드레이닝이 나머지를 다 쓰고, 인계에 20초를 쓰면 그만큼 줄어든다.
# 더하면 플랫폼 유예를 넘겨 SIGKILL 로 잘리기 때문이다.
DEFAULT_SHUTDOWN_DEADLINE_S = math.inf


async def _first_of(*events: asyncio.Event, timeout: float) -> None:
    """이벤트 중 **아무거나 하나**가 서거나 시간이 다 될 때까지 기다린다."""
    if timeout <= 0 or any(e.is_set() for e in events):
        return
    waiters = [asyncio.ensure_future(e.wait()) for e in events]
    try:
        await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for w in waiters:
            w.cancel()
        for w in waiters:
            try:
                await w
            except asyncio.CancelledError:
                pass


IncomingHook = Callable[[IncomingCall], Union[Awaitable[None], None]]
_HookT = TypeVar("_HookT", bound=IncomingHook)


def _read_forwarded_from(data: dict[str, Any]) -> str | None:
    """``call.incoming`` 의 착신전환 원번호. 직통 통화·옛 서버는 키가 없고, 빈 문자열도 「없음」이다."""
    value = data.get("forwardedFrom")
    return value if isinstance(value, str) and value else None


def _log_late_hook_error(task: asyncio.Future[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning("on_incoming 훅이 결정 뒤에 예외를 던졌습니다: %s: %s", type(exc).__name__, exc)


class ToolConfig(TypedDict, total=False):
    """Tool 실행 관련 설정."""

    hold_audio: bool | str | bytes
    """Tool 실행 중 재생할 hold audio. True=기본 차임, str=wav 파일 경로, bytes=raw ulaw."""


class ClawOpsAgent:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        account_id: str | None = None,
        base_url: str | None = None,
        from_: str,
        session: Session | None = None,
        session_factory: Callable[[], Session] | None = None,
        mcp_servers: list[Any] | None = None,
        recording: bool = False,
        recording_path: str = "./recordings",
        tracing: TracingConfig | None = None,
        builtin_tools: BuiltinTool | list[BuiltinTool] = BuiltinTool.ALL,
        passive_dtmf_debounce_ms: int = 2000,
        passive_dtmf_finish_key: str | None = "#",
        passive_dtmf_max_digits: int | None = None,
        tool_config: ToolConfig | None = None,
        rx_gain: float = 1.0,
        tx_gain: float = 1.0,
        prewarm_enabled: bool = True,
        machine_detection: Literal["Enable", "Hangup"] | None = None,
        on_taken_over: Callable[[int, str], None] | None = None,
    ) -> None:
        if api_key is None:
            api_key = os.environ.get("CLAWOPS_API_KEY")
        if api_key is None:
            raise AgentError("api_key를 지정하거나 CLAWOPS_API_KEY 환경변수를 설정하세요.")

        if account_id is None:
            account_id = os.environ.get("CLAWOPS_ACCOUNT_ID")
        if account_id is None:
            raise AgentError("account_id를 지정하거나 CLAWOPS_ACCOUNT_ID 환경변수를 설정하세요.")

        if base_url is None:
            base_url = os.environ.get("CLAWOPS_BASE_URL", "https://api.claw-ops.com")

        if (session is None) == (session_factory is None):
            raise AgentError(
                "session 또는 session_factory 중 하나만 지정하세요. "
                "동시 통화를 한 프로세스로 처리하려면 session_factory 를 쓰세요 — "
                "session 은 통화 사이에 같은 객체를 공유하므로 동시 통화 1건까지만 안전합니다."
            )

        self._api_key = api_key
        self._account_id = account_id
        self._base_url = base_url
        self._from_number = from_

        self._session = session
        self._session_factory = session_factory
        self._mcp_servers = mcp_servers or []
        self._recording = recording
        self._recording_path = recording_path
        self._tracing = tracing
        self._rx_gain = self._validate_gain("rx_gain", rx_gain)
        self._tx_gain = self._validate_gain("tx_gain", tx_gain)
        self._prewarm_enabled = prewarm_enabled
        # 모든 발신에 적용되는 AMD default. call() 인자로 호출별 override 가능.
        self._machine_detection: Literal["Enable", "Hangup"] | None = machine_detection

        if self._tracing is not None:
            setup_tracing(self._tracing)

        _hold_audio = tool_config.get("hold_audio", False) if tool_config else False
        self._hold_audio_chunks: list[bytes] | None = load_hold_audio(_hold_audio) if _hold_audio else None

        self._builtin_tools = resolve_builtin_tools(builtin_tools)
        # 키패드 자동 전달의 확정 규칙. 셋 중 먼저 오는 것으로 확정한다 — 종료키 / 자리 수 /
        # 마지막 키 뒤 대기. 기본 대기 2초는 Pipecat DTMFAggregator 와 같다. 0.5초였을 땐 사람
        # 손 간격(실측 0.33~0.94초)보다 짧아 534 가 5·3·4 로 쪼개져 들어갔다.
        if passive_dtmf_debounce_ms < 0:
            raise AgentError("passive_dtmf_debounce_ms 는 0 이상이어야 합니다.")
        if passive_dtmf_finish_key is not None and (
            len(passive_dtmf_finish_key) != 1 or passive_dtmf_finish_key not in "0123456789*#"
        ):
            raise AgentError("passive_dtmf_finish_key 는 0-9, *, # 중 한 글자이거나 None 이어야 합니다.")
        if passive_dtmf_max_digits is not None and passive_dtmf_max_digits < 1:
            raise AgentError("passive_dtmf_max_digits 는 1 이상이거나 None 이어야 합니다.")
        self._passive_dtmf_debounce_ms = passive_dtmf_debounce_ms
        self._passive_dtmf_finish_key = passive_dtmf_finish_key
        self._passive_dtmf_max_digits = passive_dtmf_max_digits
        # call_id → 그 통화가 쓰는 Session. 팩토리 모드에서는 이 표가 SoT 다 —
        # _session_for() 가 여기에 만들어 넣고, 통화 teardown 이 pop 한다.
        # 공유 모드에서는 모든 항목이 같은 self._session 을 가리킨다.
        self._call_sessions: dict[str, Session] = {}

        self._tool_registry = ToolRegistry()
        self._event_handlers: dict[str, list[Callable[..., Awaitable[None]]]] = {}
        # 수신 통화 선별 훅(@agent.on_incoming). None 이면 오늘과 똑같이 즉시 수락한다.
        self._incoming_hook: IncomingHook | None = None
        # 훅 결정 시한(초). 테스트가 줄일 수 있게 인스턴스 값으로 둔다.
        self._incoming_hook_deadline_s = INCOMING_HOOK_DEADLINE_S
        # 선별 중인 통화. 그 사이 서버가 call.ended 를 보내면(발신자가 끊음) 여기서 표시한다.
        self._screening: dict[str, bool] = {}
        # IncomingCall.transfer() 로 받은 통화의 task — 참조를 쥐고 있어야 GC 되지 않는다.
        self._transfer_only_tasks: set[asyncio.Task[None]] = set()
        self._active_sessions: dict[str, CallSession] = {}
        # 미디어 정리를 마친 통화가 서버 종료 프레임을 기다리는 자리. call_id → Event.
        self._terminal_waiters: dict[str, asyncio.Event] = {}
        self._control_ws: ControlWebSocket | None = None
        self._control_ws_task: asyncio.Task[Any] | None = None
        # 서버가 이 번호를 다른 프로세스에 넘긴 뒤 True(롤링 배포 인계).
        self._taken_over = False
        # control 연결을 **의도적으로 내놓은** 뒤 True(drain()/disconnect()).
        # `self._control_ws is None` 과 다르다 — 그쪽은 "아직 연결한 적 없음" 까지 포함한다.
        # 종료 프레임이 더는 못 온다고 확신할 수 있는 건 내놓은 뒤뿐이다.
        self._control_given_up = False
        # 서버가 control 연결을 거절했다(키 폐기·번호 회수 등). 다시 붙어도 같으니 이 에이전트는 끝이다.
        self._auth_rejected: AgentAuthRejectedError | None = None
        # 인계 통지 훅. serve() 를 쓰면 필요 없다 — connect() 를 직접 모는 쪽이
        # 그 통지를 받아 drain() 을 부를 수 있게 열어 둔 자리다.
        self._on_taken_over = on_taken_over
        # serve() 의 대기 이벤트 — 인계 통지도 시그널과 같은 방식으로 깨울 수 있게 잡아 둔다.
        self._stop_serve: asyncio.Event | None = None
        # 서버가 자리를 넘겼다고 통지했다(agent.retired). 4409 close 와 뜻은 같지만 **연결이
        # 살아 있다** — 진행 중 통화의 종료 이벤트가 이 연결로 돌아온다. 그래서 우리가 먼저
        # 끊으면 안 된다.
        self._retired_serve: asyncio.Event | None = None
        # 지금 콜을 받을 수 있는가. 파일 마커와 /healthz 가 같은 값을 본다.
        self._ready = False
        # 낡은 표시는 **패키지 import 시점에** 이미 지웠다(clawops/agent/__init__.py) — 거기가
        # 가장 이르고, 그보다 늦으면 무거운 import 구간이 창으로 남는다. 여기서 한 번 더 부르는
        # 것은 멱등이라 무해하고, 프로세스 안에서 에이전트를 다시 만드는 경로를 덮는다.
        clear_stale_ready_marker()
        # prewarm 추적 — outbound_ready 수신 시 session.prewarm() 을 백그라운드 task 로
        # 시작하고, _start_call_session 이 await + attach() 로 부착한다.
        self._prewarm_tasks: dict[str, asyncio.Task[Any]] = {}
        self._prewarm_failed: set[str] = set()
        # attach 완료된 callId — 이후의 stop() 은 정상 종료 경로가 책임진다.
        # cleanup 이 attached 통화에 중복 stop() 하지 않도록 가드한다.
        self._prewarm_attached: set[str] = set()

    @staticmethod
    def _validate_gain(name: str, gain: float) -> float:
        value = float(gain)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name}={gain!r} must be a finite number >= 0")
        return value

    def tool(
        self,
        fn: Callable[..., Awaitable[str]] | None = None,
        *,
        behavior: str | None = None,
        scheduling: str | None = None,
    ) -> Any:
        """도구 등록 데코레이터. ``@agent.tool`` 과 ``@agent.tool(behavior="NON_BLOCKING")`` 둘 다 된다.

        - ``behavior``: Gemini Live 함수 선언의 behavior(``"BLOCKING"``·``"NON_BLOCKING"``). 생략하면
          세션의 모델별 기본값. NON_BLOCKING 도구는 결과를 기다리지 않고 모델이 계속 대화하며, 결과는
          도구가 끝나는 즉시 보낸다.
        - ``scheduling``: NON_BLOCKING 결과를 모델이 다루는 방식(``"SILENT"``·``"WHEN_IDLE"``·``"INTERRUPT"``).
        """
        return self._tool_registry.register(fn, behavior=behavior, scheduling=scheduling)

    def on(self, event: str) -> Callable:
        def decorator(fn: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
            self._event_handlers.setdefault(event, []).append(fn)
            return fn

        return decorator

    def on_incoming(self, fn: _HookT) -> _HookT:
        """수신 통화를 **받기 전에** 선별하는 훅을 등록한다. 데코레이터로도, 직접 호출로도 쓴다.

        ```python
        @agent.on_incoming
        async def screen(call: IncomingCall):
            if call.caller_id_restricted:
                await call.reject()            # SIP 403. reason="busy" 면 486
        ```

        - 훅이 ``reject()`` 를 부르지 않고 끝나면 수락한다(훅이 없을 때와 같다).
        - 5초 안에 결정하지 못하거나 예외를 던지면 경고를 남기고 **수락한다**(fail-open).
        - 거절한 통화에는 세션·미디어·``session_setup`` 이 없고 ``call_start`` 등 이벤트도 없다.
        - 동기 함수도 된다. 훅은 하나만 둔다 — 다시 등록하면 앞의 것을 바꾼다.
        """
        if not callable(fn):
            raise TypeError("on_incoming 에는 호출 가능한 함수를 넘기세요")
        if self._incoming_hook is not None:
            log.warning("on_incoming 훅을 다시 등록했습니다 — 앞의 훅은 더 이상 불리지 않습니다")
        self._incoming_hook = fn
        return fn

    async def connect(self) -> None:
        """Control WS에 연결한다. 블로킹하지 않는다.

        서버가 연결을 거절하면(API 키가 폐기·만료됐거나 번호가 이 계정 것이 아님)
        ``AgentAuthRejectedError`` 를 던진다. 재시도해도 같으니 키·번호를 고친 뒤 새 에이전트를 띄운다.
        """
        if self._auth_rejected is not None:
            raise self._auth_rejected
        if self._control_ws is not None:
            return
        # 배포 환경 진단 — 동작은 안 바꾸고 조용한 실패만 시끄럽게 한다.
        # 낡은 마커 정리는 __init__ 에서 이미 했다(멱등하지만 여기서도 한 번 더 — connect()
        # 를 직접 여러 번 부르는 경로가 있다).
        clear_stale_ready_marker()
        # import 시점에 지운 낡은 표시가 있었으면 여기서 알린다 — 그때는 로깅 설정 전이라
        # 경고가 묻힌다.
        stale = take_stale_clear_notice()
        if stale:
            log.warning(
                "낡은 준비 표시를 지웠다: %s — 이전 프로세스가 남긴 것이다. 그대로 두면 새 "
                "프로세스가 연결되기도 전에 Ready 로 판정돼 그동안 오는 콜이 죽는다.",
                stale,
            )
        warn_if_signals_blocked()
        # 인계 뒤의 재연결은 복구가 아니다 — 자리는 번호당 하나뿐이라 새 연결은 방금 넘겨받은
        # 프로세스를 밀어내고, 그쪽이 다시 붙어 이쪽을 밀어낸다. call() 이 이 경로를 타므로
        # drain 중/이후의 발신 한 건이 방금 내놓은 자리를 도로 뺏는 일이 생긴다.
        if self._taken_over:
            raise AgentError(
                f"{self._from_number} 는 이제 다른 프로세스가 맡는다 — 재연결하면 그 프로세스를 "
                "밀어낸다. 이 프로세스를 되살리지 말고 새 에이전트를 띄울 것."
            )
        # 세션 프로세스 수명 셋업 (있으면). control WS task + 모든 통화 task 의 공통
        # 조상인 이 시점(root task)에서 돌아야 하는 준비 작업 — 예: LiveKitSession 이
        # HTTP 플러그인용 http_context 를 여기서 연다.
        # 팩토리 모드에는 프로세스 세션이 없다 — 훅은 통화별 세션에 대해 _open_session /
        # _close_session 이 돌린다.
        if self._session is not None and hasattr(self._session, "session_setup"):
            await self._session.session_setup()
        await self._ensure_control_ws()
        log.info(f"ClawOpsAgent connected on {self._from_number}")

    async def serve(
        self,
        *,
        drain_timeout: float = DEFAULT_DRAIN_TIMEOUT_S,
        handover_wait: float = DEFAULT_HANDOVER_WAIT_S,
        shutdown_deadline: float = DEFAULT_SHUTDOWN_DEADLINE_S,
        health_port: int | None = None,
    ) -> None:
        """인바운드 서버 모드: 멈출 때가 되면 자리를 넘기고 통화를 마친 뒤 돌아온다.

        종료 시그널을 받으면 두 국면을 지난다.

        1. **인계 대기** — 자리를 놓지 않고 계속 전화를 받는다. 후임이 붙어 서버가 자리를
           넘길 때까지(`agent.retired`), 또는 ``handover_wait`` 까지.
        2. **드레이닝** — 새 전화는 후임에게 간다. 진행 중이던 통화만 끝까지 기다린다.

        1번이 배포의 빈틈을 없앤다. 예전에는 SIGTERM 을 받는 즉시 자리를 놓았고, 그때 후임이
        아직 안 떠 있으면 그 사이 오는 전화가 전부 죽었다.

        **마감은 기본이 없다.** 진행 중이던 통화가 끝날 때까지 기다리고, 끊는 것은 플랫폼
        SIGKILL 하나뿐이다. 예전 기본값(110초)은 ECS ``stopTimeout`` 상한에서 나온 값인데 k8s
        에도 그대로 쓰였고, 그래서 **진행 중이던 통화의 29.8% 를 우리가 직접 끊고 있었다.**

        마감을 주면 규약은 그대로다 — **두 값을 더하지 않는다.** SIGTERM 부터
        ``shutdown_deadline`` 하나를 세고, 인계 대기가 쓴 만큼 드레이닝의 몫이 줄어든다.
        유예가 짧은 환경(ECS ``stopTimeout`` 은 120초가 상한)에서 유예 안에 스스로 정리하려면
        그 유예보다 조금 짧게 준다.

        ⚠️ 마감이 무한이어도 **빠져나올 길은 있다** — 종료 시그널을 두 번 더 보내면
        (2차=인계 중단, 3차=진행 중 통화 절단) 즉시 끝난다. SIGINT 는 1차가 곧 2차 몫이라
        Ctrl-C 두 번이면 끊긴다.

        ⚠️ **후임이 없을 때는 인계 대기가 손해다.** 스케일인이나 단순 정지에서는 그 대기 동안
        받은 전화가 유예 만료에 끊길 수 있다. 그래서 ``handover_wait`` 는 짧다(기본 20초).
        후임을 띄우지 않고 내리기만 하는 배포라면 ``handover_wait=0`` 으로 꺼도 된다.

        시그널 규약:

        - **SIGTERM** 1차 = 인계 대기 시작, 2차 = 인계 대기 중단하고 드레이닝, 3차 = 즉시 절단.
        - **SIGINT** 는 예전과 같다 — 인계를 기다리지 않고 곧바로 드레이닝한다. 로컬에서
          Ctrl-C 를 눌렀는데 20초를 기다리면 안 된다.

        connect() 가 호출되지 않은 상태면 자동으로 connect() 를 먼저 수행한다.
        """
        health_server = None
        if health_port is not None:
            # distroless 처럼 cat·sh 가 없는 이미지에서는 exec 프로브가 성립하지 않는다.
            # 그때의 유일한 길이라 connect() **전에** 연다 — 기동이 느려도 프로브는 붙고
            # 503 을 받는다(포트가 아예 안 열리면 프로브는 연결 거부로 실패한다).
            health_server = await start_health_server(health_port, lambda: self._ready)
        try:
            await self._serve_inner(
                drain_timeout=drain_timeout,
                handover_wait=handover_wait,
                shutdown_deadline=shutdown_deadline,
            )
        finally:
            if health_server is not None:
                health_server.close()
                await health_server.wait_closed()

    async def _serve_inner(
        self,
        *,
        drain_timeout: float,
        handover_wait: float,
        shutdown_deadline: float,
    ) -> None:
        await self.connect()
        stop_event = asyncio.Event()
        cut_event = asyncio.Event()
        retired_event = asyncio.Event()
        # 인계를 더 기다리지 않는다(2차 시그널 · SIGINT). 절단과는 다르다 — 진행 중 통화는
        # 여전히 드레이닝이 지킨다.
        skip_handover_event = asyncio.Event()
        self._stop_serve = stop_event
        self._retired_serve = retired_event
        # 인계 통지가 이미 와 있었으면(연결 직후 교체) 그 상태를 그대로 반영한다.
        if self._taken_over:
            retired_event.set()
            stop_event.set()
        # 연결 직후 거절됐어도 같다 — 아래에서 곧바로 드레이닝으로 간다.
        if self._auth_rejected is not None:
            stop_event.set()
        loop = asyncio.get_running_loop()
        signals = 0

        def _on_signal(name: str) -> None:
            # 시그널은 **단계를 하나씩 올린다.** 인계는 스스로 종료를 시작하고, 오케스트레이터가
            # 교체 대상에 보내는 SIGTERM 은 그 직후에 온다 — 그 첫 시그널을 "기다리지 말라" 로
            # 세면 지키려던 통화를 정확히 끊는다.
            #
            #   1차  종료 시작(SIGTERM 은 인계 대기부터, SIGINT 는 곧바로 드레이닝)
            #   2차  인계를 더 기다리지 않는다 → 드레이닝
            #   3차  진행 중 통화까지 끊는다
            nonlocal signals
            signals += 1
            if signals == 1:
                # 배포 사후 판별이 로그만으로 되게 한다 — 시그널을 **들었는지** 여부가
                # 셸 형태 진입점 사고의 갈림길이다(들으면 이 줄이 있고, 못 들으면 없다).
                log.info(f"{name} 수신 — 종료 절차 시작")
                if name == "SIGINT":
                    # 로컬에서 Ctrl-C 를 눌렀는데 인계를 20초 기다리면 안 된다.
                    skip_handover_event.set()
            elif not skip_handover_event.is_set():
                log.warning(f"{name}: 두 번째 종료 시그널 — 인계를 더 기다리지 않는다")
                skip_handover_event.set()
            else:
                log.warning(f"{name}: 종료 시그널 반복 — 진행 중 통화를 즉시 끊는다")
                cut_event.set()
            stop_event.set()

        # 핸들러는 드레이닝이 끝날 때까지 붙여 둔다. 먼저 떼면 그 사이(최대 마감만큼)에 오는
        # SIGTERM 이 기본 처리로 떨어져 프로세스를 즉사시키고, 진행 중 통화가 통째로 끊긴다.
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _on_signal, sig.name)
        try:
            await stop_event.wait()
            deadline_at = loop.time() + shutdown_deadline
            # 거절당한 자리는 넘길 후임도 지킬 자리도 없다 — 진행 중 통화만 마친다.
            if self._auth_rejected is not None:
                skip_handover_event.set()

            # ── 국면 1: 인계 대기 ──
            # SIGTERM 으로 내려가는 중이고, 아직 인계를 못 받았고, 절단 지시도 없을 때만.
            wait_for_handover = (
                handover_wait > 0
                and not skip_handover_event.is_set()
                and not retired_event.is_set()
                and not cut_event.is_set()
            )
            if wait_for_handover:
                # 여기부터는 무슨 이유로 끊겨도 재연결하지 않는다. 재연결하면 그 사이 자리를
                # 받은 후임을 밀어낸다 — 후임은 SIGTERM 도 안 받았는데 물러나게 된다.
                if self._control_ws:
                    self._control_ws.enter_lame_duck()
                budget = min(handover_wait, max(0.0, deadline_at - loop.time()))
                log.info(f"인계 대기 — 자리를 유지한 채 후임을 기다린다 (최대 {budget:.0f}s)")
                await _first_of(retired_event, skip_handover_event, cut_event, timeout=budget)
                if retired_event.is_set():
                    log.info("인계 완료 — 새 전화는 후임이 받는다")
                else:
                    log.info("인계 통지 없음 — 자리를 놓고 드레이닝으로 넘어간다")

            # ── 국면 2: 드레이닝 ──
            if cut_event.is_set():
                await self.disconnect()
                return

            # 인계를 받았으면 자리는 이미 서버가 가져갔다 — 연결을 **유지**해야 진행 중이던
            # 통화의 종료 이벤트(ended_duration 포함)가 이 연결로 돌아온다. 인계가 없었으면
            # 우리가 직접 자리를 놓아야 새 전화가 다른 곳으로 간다.
            release_slot = not retired_event.is_set()
            budget = max(0.0, min(drain_timeout, deadline_at - loop.time()))
            drain_task = asyncio.ensure_future(
                self.drain(timeout=budget, release_slot=release_slot)
            )
            cut_task = asyncio.ensure_future(cut_event.wait())
            try:
                await asyncio.wait({drain_task, cut_task}, return_when=asyncio.FIRST_COMPLETED)
                if not drain_task.done():
                    drain_task.cancel()
                    await self.disconnect()
            finally:
                cut_task.cancel()
                for task in (drain_task, cut_task):
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
        finally:
            self._stop_serve = None
            self._retired_serve = None
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
        # 조용히 돌아가면 프로세스가 정상 종료(0)로 끝나 거절이 묻힌다 — 던져서 실패로 끝낸다.
        if self._auth_rejected is not None:
            raise self._auth_rejected

    def _handle_taken_over(self, code: int, reason: str) -> None:
        """서버가 이 번호의 control 연결을 다른 프로세스에 넘겼다.

        오류가 아니다 — 교체되는 쪽에서 본 롤링 배포의 한가운데다. 새 콜은 이미 새
        프로세스로 가고 있으니, 남은 건 들고 있던 통화를 마치고 비켜 주는 것뿐이다.
        serve() 는 반환하는 것으로 그렇게 하고, connect() 를 직접 쓰는 쪽은
        ``on_taken_over`` 로 통지받아 drain() 을 부르면 된다.
        """
        self._taken_over = True
        self._ready = False
        remove_ready_marker()
        log.info(f"{self._from_number} 는 이제 다른 프로세스가 맡는다 (close {code}) — 인계")
        if self._on_taken_over:
            try:
                self._on_taken_over(code, reason)
            except Exception:
                log.exception("on_taken_over 콜백 실패")
        # 연결은 이미 닫혔지만 "자리를 넘겼다" 는 사실은 같다 — serve() 가 자리를 또 놓으려
        # 하지 않게 표시한다.
        if self._retired_serve is not None:
            self._retired_serve.set()
        if self._stop_serve is not None:
            self._stop_serve.set()

    def _handle_rejected(self, err: AgentAuthRejectedError) -> None:
        """서버가 control 재연결을 거절했다 — 실행 중에 키가 폐기됐거나 번호가 회수됐다.

        다시 붙어도 같으니 콜을 더 받을 수 없다. 준비 표시를 내리고 serve() 를 깨워
        진행 중 통화만 마치고 ``AgentAuthRejectedError`` 로 끝나게 한다.
        """
        self._auth_rejected = err
        self._ready = False
        remove_ready_marker()
        if self._stop_serve is not None:
            self._stop_serve.set()

    def _handle_retired(self, reason: str) -> None:
        """서버가 자리를 넘겼다고 알려왔다(`agent.retired`) — **연결은 살아 있다.**

        4409 close 와 뜻은 같지만 끊기지 않았다. 서버가 진행 중이던 통화의 종료 이벤트를
        이 연결로 돌려주려고 남겨 둔 것이다. 그러니 여기서 끊지 않는다.

        종료 시그널 없이 이게 왔다면 우리가 아는 배포가 아니다 — 같은 번호로 인스턴스를
        둘 이상 띄웠을 때가 대부분이다(replicas > 1). 로그에 그 의심을 남긴다.
        """
        self._taken_over = True
        # 자리를 넘겼다 = 더 이상 새 콜을 받지 않는다. 프로브가 떨어져야 오케스트레이터가
        # 이 인스턴스를 뒤로 뺀다.
        self._ready = False
        remove_ready_marker()
        log.info(f"{self._from_number} 는 이제 다른 프로세스가 맡는다 ({reason}) — 인계")
        if self._stop_serve is not None and not self._stop_serve.is_set():
            log.warning(
                "종료 시그널 없이 인계가 왔다 — 같은 번호로 인스턴스가 둘 이상 떠 있지 않은지 "
                "확인할 것(replicas 는 1 이어야 한다)"
            )
        if self._on_taken_over:
            try:
                self._on_taken_over(CLOSE_REPLACED, reason)
            except Exception:
                log.exception("on_taken_over 콜백 실패")
        if self._retired_serve is not None:
            self._retired_serve.set()
        if self._stop_serve is not None:
            self._stop_serve.set()

    @property
    def taken_over(self) -> bool:
        """다른 프로세스가 이 번호의 control 연결을 넘겨받았는가."""
        return self._taken_over

    async def drain(
        self, *, timeout: float = DEFAULT_DRAIN_TIMEOUT_S, release_slot: bool = True
    ) -> tuple[int, int]:
        """새 콜 수신을 멈추고, 이미 진행 중인 통화가 끝나기를 기다린 뒤 종료한다.

        롤링 배포에 필요한 것이 이것이다. ``disconnect()`` 는 통화 도중에 끊는다 —
        "지금 멈춰라" 라는 뜻일 때는 맞고 "넘겨라" 라는 뜻일 때는 틀리다. SIGTERM 이
        둘 중 무엇이었는지는 부르는 쪽만 알기에 둘을 갈라 둔다.

        가능한 이유는 control 과 미디어가 별개 연결이기 때문이다. control WS 를 닫는 것은
        이 번호의 **배달 자리**를 내놓는 것뿐이라, 서버는 더 이상 ``call.incoming`` 을 보내지
        않고 다음 자리 주인에게 콜을 넘긴다. 이미 붙은 통화는 통화별 미디어 연결로 계속
        흐르고(여기서 건드리지 않는다), 발신자가 끊으면 각자 정상적으로 정리된다.

        한 가지는 포기한다: ``call_end`` 의 ``ended_duration``. 그 값은 방금 닫은 control
        연결을 타고 오므로, drain 중에 끝나는 통화는 duration 이 None 으로 보고된다.

        Args:
            timeout: 진행 중 통화를 기다리는 상한(초). **기본은 무한** — 통화가 끝날 때까지
                기다리고, 끊는 것은 플랫폼 SIGKILL 하나뿐이다. 값을 주면 그 시간이 지난 뒤
                남은 통화를 ``disconnect()`` 와 같은 방식으로 끊는다. 유예가 짧은 환경(ECS
                ``stopTimeout`` 은 120초가 상한)에서 유예 안에 스스로 정리하고 싶을 때만 준다.
            release_slot: 배달 자리를 우리가 놓을지. 서버가 이미 인계 통지(`agent.retired`)를
                보냈다면 자리는 이미 넘어갔으므로 **False** 로 두어 연결을 유지한다 — 그래야
                진행 중이던 통화의 종료 이벤트가 이 연결로 돌아온다(`ended_duration` 포함).
                인계 없이 우리가 먼저 내려가는 경우에만 True 다.

        Returns:
            (스스로 끝난 통화 수, 상한에 걸려 끊은 통화 수).
        """
        # 드레이닝 = 새 콜을 안 받는다. 인계로 이미 지워졌을 수 있지만 멱등하다.
        self._ready = False
        remove_ready_marker()
        if release_slot:
            # 배달 자리부터 내놓는다. 이 뒤에 도착하는 콜은 전부 다른 곳으로 간다.
            self._control_given_up = True
            if self._control_ws:
                await self._control_ws.close()
                self._control_ws = None
            if self._control_ws_task and not self._control_ws_task.done():
                self._control_ws_task.cancel()
                self._control_ws_task = None
            # 닫힌 제어 연결로는 종료 프레임이 올 수 없다 — 이미 기다리는 통화를 지금 깨운다.
            # drain 중에 **나중에** 끝나는 통화는 _await_server_terminal 의 _control_given_up 검사가
            # 맡는다. 그게 없으면 그 통화들은 아무도 못 깨우는 waiter 를 새로 등록하고
            # 유예를 통째로 헛쓴다.
            for waiter in list(self._terminal_waiters.values()):
                waiter.set()
        else:
            # 자리는 서버가 이미 가져갔다(agent.retired). 연결은 그대로 둔다 — 진행 중이던
            # 통화의 call.ended 가 이 연결로 돌아오고, 그래야 ended_duration 이 채워진다.
            # 예전에는 여기서 무조건 끊었기 때문에 drain 중 끝난 통화의 duration 이 항상
            # None 이었다.
            log.info("자리는 이미 넘어갔다 — 연결을 유지한 채 진행 중 통화만 기다린다")

        in_flight = len(self._active_sessions)
        if in_flight == 0:
            log.info("Drain: 진행 중인 통화 없음")
            await self.disconnect()
            return (0, 0)

        limit_txt = "상한 없음 — 플랫폼 유예까지" if math.isinf(timeout) else f"상한 {timeout}s"
        log.info(f"Drain 시작: 진행 중인 통화 {in_flight}건이 끝나기를 기다린다 ({limit_txt})")
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + timeout
        while self._active_sessions and loop.time() < deadline:
            await asyncio.sleep(DRAIN_POLL_INTERVAL_S)

        elapsed = loop.time() - started
        forced = len(self._active_sessions)
        if forced:
            log.warning(
                f"Drain 상한 초과({elapsed:.1f}s) — 진행 중이던 {forced}건을 끊는다. "
                f"플랫폼 유예(terminationGracePeriodSeconds / stopTimeout)가 상한 {timeout}s "
                "보다 길어야 이 절단이 사라진다 (상한을 안 주면 기본이 무한이다)"
            )
        else:
            log.info(f"Drain 완료({elapsed:.1f}s): {in_flight}건 모두 정상 종료")

        await self.disconnect()
        return (in_flight - forced, forced)

    async def disconnect(self) -> None:
        """Control WS 닫기 + 활성 세션 정리."""
        self._ready = False
        remove_ready_marker()
        self._control_given_up = True
        if self._control_ws:
            await self._control_ws.close()
            self._control_ws = None
        if self._control_ws_task and not self._control_ws_task.done():
            self._control_ws_task.cancel()
            self._control_ws_task = None
        # 제어 연결이 닫힌 뒤엔 종료 프레임이 올 수 없다 — 기다리는 통화를 즉시 깨우지 않으면
        # 종료 중인 통화마다 상한(2초)을 통째로 헛쓴다. ControlWebSocket.close() 가 대기 중인
        # 전환 future 를 정리하는 것과 같은 규율이다.
        for waiter in list(self._terminal_waiters.values()):
            waiter.set()
        self._active_sessions.clear()
        # 팩토리 모드에서 아직 안 치워진 통화 세션이 있으면 각자의 teardown 을 돌린다.
        for call_id in list(self._call_sessions):
            await self._close_session(call_id)
        self._call_sessions.clear()
        # session_setup 의 짝 — 같은 root task 에서 정리한다 (예: http_context 닫기).
        if self._session is not None and hasattr(self._session, "session_teardown"):
            await self._session.session_teardown()
        log.info("ClawOpsAgent disconnected")

    # ── 통화별 Session 수명 ────────────────────────────────────────

    async def _open_session(self, call_id: str) -> Session:
        """이 통화가 쓸 Session 을 확정해 등록한다. 통화가 잡히는 즉시 호출한다.

        공유 모드(`session=`)는 생성자가 받은 객체를 그대로 쓴다 — 모든 통화가 같은
        인스턴스를 가리키므로 동시 통화 1건까지만 안전하다.
        팩토리 모드(`session_factory=`)는 통화마다 새로 만든다. 대화 이력·오디오 큐·
        전송 대상이 통화 안에 갇히므로 동시 통화가 서로를 덮어쓰지 않는다.
        """
        shared = self._session
        if shared is not None:  # 공유 모드 — 생성자가 factory 와 배타임을 보장한다
            if self._call_sessions and call_id not in self._call_sessions:
                log.error(
                    "동시 통화 %d건이 같은 Session 객체를 공유한다 (call_id=%s). 뒤에 시작한 "
                    "통화가 앞선 통화의 대화 이력과 오디오 경로를 덮어쓴다 — "
                    "ClawOpsAgent(session_factory=...) 로 바꾸면 통화별로 격리된다.",
                    len(self._call_sessions) + 1,
                    call_id,
                )
            self._call_sessions[call_id] = shared
            return shared

        assert self._session_factory is not None
        session = self._call_sessions.get(call_id)
        if session is None:
            session = self._session_factory()
            self._call_sessions[call_id] = session
            # 공유 모드에서 connect() 가 돌리는 프로세스 훅의 통화판. 이 통화 task 에서
            # 열었으므로 하위 STT/TTS task 가 contextvar 를 그대로 상속받는다.
            if hasattr(session, "session_setup"):
                await session.session_setup()
        return session

    async def _close_session(self, call_id: str) -> None:
        """통화가 끝났을 때 그 통화의 Session 을 등록 해제하고 훅을 돌린다."""
        session = self._call_sessions.pop(call_id, None)
        if session is None or self._session_factory is None:
            return
        if hasattr(session, "session_teardown"):
            try:
                await session.session_teardown()
            except Exception as e:
                log.warning(f"session_teardown failed for {call_id}: {e}")

    def _session_for(self, call_id: str) -> Session:
        """이 통화의 Session. self._session 을 직접 읽지 않는 유일한 통로다.

        등록 전에 불릴 수 있는 경로(테스트·전송 직전 실패 등)를 위해 공유 모드는
        생성자가 받은 객체로 폴백한다. 팩토리 모드에는 폴백이 없다 — 여기서 새로
        만들면 통화가 끝난 뒤의 정리 코드가 세션을 되살리게 된다.
        """
        session = self._call_sessions.get(call_id)
        if session is not None:
            return session
        if self._session is not None:
            return self._session
        raise AgentError(f"통화 {call_id} 의 Session 이 등록되지 않았다")

    async def _ensure_control_ws(self) -> None:
        """Control WS가 없으면 연결한다."""
        if self._control_ws is not None:
            return
        self._control_given_up = False
        self._control_ws = ControlWebSocket(
            base_url=self._base_url,
            api_key=self._api_key,
            account_id=self._account_id,
            number=self._from_number,
            on_call_incoming=self._handle_incoming,
            on_call_ended=self._handle_ended,
            on_call_outbound_ready=self._handle_outbound_ready,
            on_call_ringing=self._handle_ringing,
            on_call_failed=self._handle_failed,
            on_terminal_close=self._handle_taken_over,
            on_retired=self._handle_retired,
            on_rejected=self._handle_rejected,
        )
        self._control_ws_task = asyncio.create_task(self._control_ws.connect())
        try:
            await self._control_ws.wait_connected()
        except AgentAuthRejectedError:
            # 첫 연결부터 거절 — 연결 태스크는 이미 멈췄다. 붙은 적이 없으니 자리를 비워 둔다.
            self._control_ws = None
            self._control_ws_task = None
            raise
        try:
            await self._control_ws.send({"event": "agent.hello", "sdk": get_sdk_info()})
        except Exception:
            pass
        # 여기가 "콜을 받을 수 있게 된 시점" 이다 — 그걸 아는 건 SDK 뿐이라 SDK 가 표시한다.
        # 예전에는 고객 앱이 connect() 뒤에 직접 파일을 만들어야 했다.
        self._ready = True
        write_ready_marker()

    async def call(
        self,
        to: str,
        *,
        timeout: int = 60,
        machine_detection: Literal["Enable", "Hangup"] | None = None,
    ) -> CallSession:
        """발신 전화를 건다. CallSession을 즉시 리턴 (queued 상태).

        connect()가 호출되지 않은 상태면 자동으로 connect()를 먼저 수행한다.

        Args:
            to: 착신 번호.
            timeout: 발신 ring timeout (초).
            machine_detection: 자동응답기/음성사서함 감지(AMD). ``"Enable"`` 이면 감지 후
                ``AnsweredBy`` 통보(통화 계속), ``"Hangup"`` 이면 음성사서함 감지 시 자동 종료.
                ``None`` (기본)이면 인스턴스 default(생성자의 ``machine_detection``)를 따른다.
                우선순위: 호출 인자 > 인스턴스 default > 비활성.
        """
        await self.connect()

        import aiohttp as _aiohttp

        url = f"{self._base_url}/v1/accounts/{self._account_id}/calls"
        body: dict[str, object] = {"To": to, "From": self._from_number, "Timeout": timeout}
        effective_md = machine_detection if machine_detection is not None else self._machine_detection
        if effective_md in ("Enable", "Hangup"):
            body["MachineDetection"] = effective_md
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        async with _aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers) as resp:
                if resp.status != 201:
                    error = await resp.json()
                    err_msg = error.get("error", "")
                    err_code = error.get("code", "")
                    agent_err = AgentError(f"발신 실패 ({resp.status}): {err_msg}")
                    agent_err.status = resp.status
                    agent_err.code = err_code
                    raise agent_err
                data = await resp.json()

        call_session = CallSession(
            call_id=data["callId"],
            from_number=self._from_number,
            to_number=to,
            account_id=self._account_id,
            direction="outbound",
        )

        for event, handlers in self._event_handlers.items():
            for handler in handlers:
                call_session.on(event, handler)

        self._active_sessions[call_session.call_id] = call_session
        # prewarm 보다 먼저 이 통화의 Session 을 확정한다 — _start_prewarm 이
        # tools_frozen_after_prewarm 을 그 인스턴스에서 읽는다.
        await self._open_session(call_session.call_id)
        log.info(f"Outbound call initiated: {self._from_number} -> {to} ({call_session.call_id})")

        # originate 직후 prewarm 을 시작한다 — ring 구간(answer 이전)에 LLM 연결 +
        # greeting 생성을 흡수해 answer→first-audio latency 를 줄인다. call.ringing
        # 이벤트는 트렁크가 SIP 18x 를 안 올리면 도착하지 않을 수 있어 신뢰하지 않는다.
        # ringing/outbound_ready 핸들러의 prewarm 시작은 이 시점을 놓쳤을 때의 fallback.
        self._start_prewarm(call_session.call_id)

        return call_session

    async def _handle_incoming(self, data: dict[str, Any]) -> None:
        if self._incoming_hook is None:
            await self._accept_incoming(data)
            return
        # 훅은 최대 5초를 쓴다. control WS 수신 루프는 이벤트를 하나씩 await 하므로 여기서
        # 기다리면 그동안 다른 통화의 call.incoming·call.ended 가 멈춘다 — 따로 돌린다.
        asyncio.create_task(self._screen_incoming(data))

    async def _screen_incoming(self, data: dict[str, Any]) -> None:
        call_id = data["callId"]
        incoming = IncomingCall(
            call_id=call_id,
            from_number=data.get("from", ""),
            to_number=self._from_number,
            caller_id_restricted=data.get("callerIdRestricted") is True,
            forwarded_from=_read_forwarded_from(data),
        )
        self._screening[call_id] = False
        try:
            reason = await self._run_incoming_hook(incoming)
        finally:
            ended = self._screening.pop(call_id, False)

        if reason is not None:
            log.info(f"Incoming call rejected by on_incoming: {call_id} (reason={reason})")
            if self._control_ws:
                await self._control_ws.send({"event": "call.reject", "callId": call_id, "reason": reason})
            return
        if ended:
            # 선별하는 사이 발신자가 끊었다 — 수락할 통화가 없다.
            log.info(f"Incoming call ended during on_incoming screening: {call_id}")
            return
        if incoming.transfer_request is not None:
            await self._accept_for_transfer(data, incoming.transfer_request)
            return
        await self._accept_incoming(data)

    async def _accept_for_transfer(self, data: dict[str, Any], transfer: dict[str, Any]) -> None:
        """``IncomingCall.transfer()`` — AI 세션 없이 받아서 곧바로 넘긴다.

        Session 은 열지 않는다(``_open_session`` 을 부르지 않는다). 통화 이벤트(``call_start`` 등)도
        없다. 서버의 ``call.ended`` 가 상태를 채울 수 있게 ``_active_sessions`` 에만 올린다.
        """
        call_id = data["callId"]
        call = CallSession(
            call_id=call_id,
            from_number=data.get("from", ""),
            to_number=self._from_number,
            account_id=self._account_id,
            direction="inbound",
            caller_id_restricted=data.get("callerIdRestricted") is True,
            forwarded_from=_read_forwarded_from(data),
        )
        log.info(f"Incoming call → transfer without AI: {call.from_number} -> {transfer.get('to')} ({call_id})")
        self._active_sessions[call_id] = call
        if self._control_ws:
            await self._control_ws.send({"event": "call.accept", "callId": call_id})
        task = asyncio.create_task(self._run_transfer_only(call, data.get("mediaUrl", ""), transfer))
        self._transfer_only_tasks.add(task)
        task.add_done_callback(self._transfer_only_tasks.discard)

    async def _run_transfer_only(self, call: CallSession, media_url: str, transfer: dict[str, Any]) -> None:
        """미디어만 붙이고, 서버가 통화를 받으면(발신자 음성이 흐르면) 전환을 요청한다.

        서버는 미디어 WS 가 붙은 **뒤에** 통화를 받고 전환 가능한 상태(mediaSession)를 만든다.
        그 전의 전환은 ``No active media session`` 으로 거절되므로 첫 음성 프레임을 기다리고,
        그래도 거절되면 짧게 다시 시도한다.

        요청이 나간 뒤에는 SDK 가 통화를 끊지 않는다 — 전환·실패 안내·종료는 서버 몫이고, 끝나면
        서버가 미디어를 닫는다. SDK 가 끊는 경우는 둘뿐이다: 요청을 아예 보내지 못했을 때, 그리고
        시작 전 거절(사유가 있는 failed: 번호 오류 등)이라 서버가 통화를 그대로 두었을 때.
        그동안 ``_active_sessions`` 에 남아 있으므로 drain 은 이 통화가 끝나기를 기다린다.
        """
        answered = asyncio.Event()

        async def on_audio(_ulaw: bytes, _ts: int) -> None:
            answered.set()

        async def on_start(_info: dict[str, Any]) -> None:
            log.info(f"Media stream started (transfer only): {call.call_id}")

        async def on_stop() -> None:
            log.info(f"Media stream stopped (transfer only): {call.call_id}")

        media_ws = MediaWebSocket(url=media_url, on_audio=on_audio, on_start=on_start, on_stop=on_stop)
        conn = asyncio.create_task(media_ws.connect())
        hang_up = False
        try:
            waiter = asyncio.create_task(answered.wait())
            try:
                done, _ = await asyncio.wait(
                    {conn, waiter}, timeout=TRANSFER_ONLY_ANSWER_WAIT_S, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                waiter.cancel()
            if conn in done:
                exc = conn.exception() if not conn.cancelled() else None
                if exc is not None:
                    log.error(f"transfer-only: 미디어 연결 실패: {call.call_id} ({type(exc).__name__}: {exc})")
                else:
                    log.info(f"전환 전에 미디어가 닫혔다(발신자가 끊음 등): {call.call_id}")
                return
            if not done:
                log.warning(
                    f"{TRANSFER_ONLY_ANSWER_WAIT_S:.0f}초 안에 발신자 음성이 오지 않았다 — 그대로 전환을 시도한다: {call.call_id}"
                )

            deadline = time.monotonic() + TRANSFER_ONLY_RETRY_S
            while True:
                control_ws = self._control_ws
                if control_ws is None:
                    log.error(f"전환 불가 — control 연결이 없다. 통화를 끊는다: {call.call_id}")
                    hang_up = True
                    return
                req = asyncio.create_task(control_ws.request_transfer(call.call_id, transfer))
                try:
                    await asyncio.wait({req, conn}, return_when=asyncio.FIRST_COMPLETED)
                except asyncio.CancelledError:
                    req.cancel()
                    raise
                if not req.done():
                    # 서버가 미디어를 닫았다 = 전환이 시작돼 AI 자리가 빠졌거나 통화가 끝났다. 남은 일은 서버 몫이다.
                    req.cancel()
                    await asyncio.gather(req, return_exceptions=True)
                    log.info(f"transfer-only: 미디어 종료 — 전환은 서버가 이어간다: {call.call_id}")
                    return
                if req.cancelled() or not isinstance(req.exception(), (type(None), AgentConnectionError)):
                    # 요청은 나갔는데 control 연결이 끊겨 결과를 못 받았다(drain·재연결). 서버 쪽 전환은 계속된다.
                    log.warning(f"transfer-only: 전환 결과를 받지 못했다 — 서버가 끝내기를 기다린다: {call.call_id}")
                    break
                if isinstance(req.exception(), AgentConnectionError):
                    log.error(f"transfer-only: 전환 요청을 보내지 못했다 — 통화를 끊는다: {call.call_id}")
                    hang_up = True
                    return
                result = req.result()
                reason = str(result.get("reason") or "")
                if result.get("status") == "failed" and NO_MEDIA_SESSION_REASON in reason and time.monotonic() < deadline:
                    await asyncio.sleep(TRANSFER_ONLY_RETRY_INTERVAL_S)
                    continue
                log.info(f"전환 결과: {call.call_id} {result}")
                if result.get("status") == "failed" and reason:
                    # 시작도 못 한 거절(번호 오류·발신번호 거절 등) — 서버는 통화를 그대로 둔다. 무음으로 남기지 않는다.
                    hang_up = True
                    return
                break
            # 서버가 끝내고 미디어를 닫을 때까지 기다린다(실패 안내 재생 포함). 우리가 먼저 닫지 않는다.
            await asyncio.gather(conn, return_exceptions=True)
        finally:
            if hang_up and not conn.done():
                # 미디어 WS 를 닫으면 서버가 발신자 채널을 끊는다(보낼 음성이 없으니 drain 할 것도 없다).
                await media_ws.close()
            if not conn.done():
                conn.cancel()
            await asyncio.gather(conn, return_exceptions=True)
            self._active_sessions.pop(call.call_id, None)

    async def _run_incoming_hook(self, incoming: IncomingCall) -> str | None:
        """훅을 돌려 거절 사유를 돌려준다. 수락이면 None.

        ``reject()`` 가 불리는 즉시 결정한다 — 훅이 그 뒤로 더 일하는 것을 기다리지 않는다.
        시한 안에 결정이 없으면 수락하고, 훅은 끊지 않고 그대로 둔다(시한 뒤의 reject 는 경고).
        """
        hook = self._incoming_hook
        assert hook is not None
        decided = asyncio.Event()
        incoming._on_decide = decided.set
        task: asyncio.Future[Any] | None = None
        logged = False
        try:
            try:
                result = hook(incoming)
                if inspect.isawaitable(result):
                    task = asyncio.ensure_future(result)
            except Exception as e:
                log.warning(
                    "on_incoming 훅이 예외를 던졌습니다 — 수락합니다: %s (%s: %s)",
                    incoming.call_id,
                    type(e).__name__,
                    e,
                    exc_info=True,
                )
                return incoming.reject_reason

            if task is not None and not decided.is_set():
                waiter = asyncio.ensure_future(decided.wait())
                try:
                    done, _ = await asyncio.wait(
                        {task, waiter},
                        timeout=self._incoming_hook_deadline_s,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    waiter.cancel()
                if not done:
                    log.warning(
                        "on_incoming 훅이 %.1f초 안에 결정하지 않았습니다 — 수락합니다: %s",
                        self._incoming_hook_deadline_s,
                        incoming.call_id,
                    )
                elif task in done and not decided.is_set() and not task.cancelled():
                    exc = task.exception()
                    if exc is not None:
                        logged = True
                        log.warning(
                            "on_incoming 훅이 예외를 던졌습니다 — 수락합니다: %s (%s: %s)",
                            incoming.call_id,
                            type(exc).__name__,
                            exc,
                            exc_info=exc,
                        )
            return incoming.reject_reason
        finally:
            incoming._closed = True
            incoming._on_decide = None
            if task is not None and not logged:
                # 결정 뒤에도 훅은 끝까지 돈다 — 그 예외가 "never retrieved" 로 묻히지 않게 한다.
                task.add_done_callback(_log_late_hook_error)

    async def _accept_incoming(self, data: dict[str, Any]) -> None:
        call_id = data["callId"]
        from_number = data.get("from", "")
        media_url = data.get("mediaUrl", "")

        log.info(f"Incoming call: {from_number} -> {self._from_number} ({call_id})")

        call = CallSession(
            call_id=call_id,
            from_number=from_number,
            to_number=self._from_number,
            account_id=self._account_id,
            direction="inbound",
            # 이 필드를 모르는 서버는 키를 보내지 않는다 — bool True 가 아니면 False.
            caller_id_restricted=data.get("callerIdRestricted") is True,
            # 착신전환 원번호(clawops#1423). 직통 통화·옛 서버는 키가 없다 → None.
            forwarded_from=_read_forwarded_from(data),
        )

        for event, handlers in self._event_handlers.items():
            for handler in handlers:
                call.on(event, handler)

        self._active_sessions[call_id] = call
        await self._open_session(call_id)

        if self._control_ws:
            await self._control_ws.send({"event": "call.accept", "callId": call_id})

        asyncio.create_task(self._safe_start_call_session(call, media_url))

    async def _safe_start_call_session(self, call: CallSession, media_url: str) -> None:
        """_start_call_session 의 예외를 잡아 control WS 로 call.session_failed 전송한다.

        OpenAI/Gemini API 키 누락 등 session.start() 단계 실패는 media WS connect
        에 도달하지 못해 call-engine 이 30 초간 무음 통화를 유지하게 만든다. 서버에
        즉시 알려서 fail-fast 시키고 _active_sessions 에서 정리한다.
        """
        try:
            await self._start_call_session(call, media_url)
        except Exception as e:
            log.error(
                "Session start failed for %s: %s",
                call.call_id,
                e,
                exc_info=True,
            )
            if self._control_ws:
                try:
                    await self._control_ws.send(
                        {
                            "event": "call.session_failed",
                            "callId": call.call_id,
                            "reason": type(e).__name__,
                            "message": str(e),
                        }
                    )
                except Exception:
                    pass
            self._active_sessions.pop(call.call_id, None)
            await self._close_session(call.call_id)

    def _inject_session_deps(
        self,
        session: Any,
        tools: ToolRegistry,
        *,
        recorder: AudioRecorder | None = None,
    ) -> None:
        """세션에 콜별 의존성(도구·내장도구·hold audio·recorder)을 duck-typing 으로 주입한다.

        prewarm 과 _start_call_session 양쪽에서 부른다. prewarm 은 세션이 LLM 에
        보낼 tool 스키마를 그 시점에 확정하므로(OpenAI session.update / Gemini live
        connect config), prewarm **전에** 최소 한 번은 도구가 들어가 있어야 한다.
        """
        if hasattr(session, "set_tool_registry"):
            session.set_tool_registry(tools)
        if recorder is not None and hasattr(session, "set_recorder"):
            session.set_recorder(recorder)
        if hasattr(session, "set_builtin_tools"):
            session.set_builtin_tools(self._builtin_tools)
        if self._hold_audio_chunks and hasattr(session, "set_hold_audio"):
            session.set_hold_audio(self._hold_audio_chunks)

    def _start_prewarm(self, call_id: str) -> None:
        """prewarm task 를 시작한다. 같은 callId 로 여러 번 불러도 안전하다.

        MCP 도구는 통화 시작 시점(_start_call_session)에야 등록되는데, 연결 후 도구
        변경이 불가능한 세션(Gemini Live)은 prewarm 하면 MCP 도구를 영영 못 쓴다.
        그런 조합에서는 prewarm 을 건너뛰고 기존 start() 경로로 간다.
        """
        if not self._prewarm_enabled or call_id in self._prewarm_tasks:
            return
        if self._mcp_servers and getattr(
            self._session_for(call_id), "tools_frozen_after_prewarm", False
        ):
            log.info(
                "Skipping prewarm for %s — 세션이 연결 후 도구 변경을 지원하지 않아 "
                "MCP 도구가 누락된다.",
                call_id,
            )
            return
        self._prewarm_tasks[call_id] = asyncio.create_task(self._prewarm_session(call_id))

    async def _start_call_session(self, call: CallSession, media_url: str) -> None:
        with call_span(
            call.call_id,
            from_number=call.from_number,
            to_number=call.to_number,
        ):
            recorder: AudioRecorder | None = None
            if self._recording:
                recorder = AudioRecorder(self._recording_path, call.call_id)
                recorder.start()

            # 콜별 독립 ToolRegistry (동시 통화 간 MCP tool 충돌 방지)
            call_tools = self._tool_registry.fork()

            mcp_clients: list[MCPClient] = []
            if self._mcp_servers:
                log.debug("Starting %d MCP server(s) for call %s", len(self._mcp_servers), call.call_id)
                for server_config in self._mcp_servers:
                    from .mcp._stdio import MCPServerStdio as _Stdio

                    if isinstance(server_config, _Stdio):
                        span_ctx = mcp_connect_span("stdio", command=server_config.command)
                    else:
                        span_ctx = mcp_connect_span("http", url=server_config.url)
                    with span_ctx:
                        client = MCPClient(server_config)
                        await client.connect()
                        mcp_clients.append(client)
                call_tools.register_mcp_tools(mcp_clients)

            session = await self._open_session(call.call_id)
            self._inject_session_deps(session, call_tools, recorder=recorder)

            latest_media_ts = 0

            async def on_audio(ulaw: bytes, ts: int) -> None:
                nonlocal latest_media_ts
                latest_media_ts = ts
                ulaw = apply_ulaw_gain(ulaw, self._rx_gain)
                if recorder:
                    recorder.write_inbound(ulaw_to_pcm16(ulaw), media_ts_ms=ts)
                await session.feed_audio(ulaw, ts)

            async def on_dtmf(digit: str) -> None:
                self._on_dtmf_event(call, digit)

            media_ws = MediaWebSocket(
                url=media_url,
                on_audio=on_audio,
                on_start=lambda info: self._on_media_start(call, info),
                on_stop=lambda: self._on_media_stop(call, session),
                on_dtmf=on_dtmf,
            )

            async def send_audio(ulaw: bytes) -> None:
                ulaw = apply_ulaw_gain(ulaw, self._tx_gain)
                if recorder:
                    recorder.write_outbound(ulaw_to_pcm16(ulaw), media_ts_ms=latest_media_ts)
                await media_ws.send_audio(ulaw)

            # drain() 은 이 통화가 도는 중에 control 연결을 None 으로 만든다. 그래서 참조는
            # 전환마다 다시 읽고 검사해야 한다 — 그냥 self._control_ws 를 부르면 drain 중의
            # 전환이 사용자 툴 밖으로 맨 AttributeError 를 던진다.
            def _transfer(params: dict[str, Any]) -> Awaitable[dict[str, Any]]:
                control_ws = self._control_ws
                if control_ws is None:
                    raise AgentError(
                        "transfer 불가: control 연결이 닫혀 있다"
                        "(drain 중이거나 이 번호가 다른 프로세스로 인계됐다)"
                    )
                return control_ws.request_transfer(call.call_id, params)

            def transfer_connection_lost() -> asyncio.Future:
                control_ws = self._control_ws
                if control_ws is None:
                    done: asyncio.Future = asyncio.get_running_loop().create_future()
                    done.set_result(None)
                    return done
                return control_ws.connection_lost()

            call._transfer_connection_lost_fn = transfer_connection_lost
            call.bind_transport(
                send_audio=send_audio,
                send_clear=media_ws.send_clear,
                hangup=media_ws.graceful_close,
                send_dtmf=media_ws.send_dtmf,
                media_ws=media_ws,
                transfer=_transfer,
            )

            await call._emit("call_start")

            # prewarm task 가 있으면 await 후 attach(), 없거나 실패면 기존 start() 경로.
            prewarm_task = self._prewarm_tasks.pop(call.call_id, None)
            prewarm_failed = call.call_id in self._prewarm_failed
            self._prewarm_failed.discard(call.call_id)
            if prewarm_task is not None and not prewarm_failed:
                try:
                    await prewarm_task
                    log.info(
                        f"[PREWARM-T] attach call_id={call.call_id} t={time.monotonic():.3f}"
                    )
                    await session.attach(call)
                    # attach 완료 — 이후 stop() 은 이 경로(finally)가 책임진다.
                    # _handle_ended/_handle_failed 의 cleanup 이 중복 stop() 하지 않도록 표시.
                    self._prewarm_attached.add(call.call_id)
                except Exception as e:
                    log.warning(f"prewarm await/attach failed, falling back to start: {e}")
                    # cancel + 명시적 수거 (B4: leak/warning 방지)
                    if not prewarm_task.done():
                        prewarm_task.cancel()
                    await asyncio.gather(prewarm_task, return_exceptions=True)
                    await session.start(call)
            else:
                if prewarm_task is not None and not prewarm_task.done():
                    prewarm_task.cancel()
                if prewarm_task is not None:
                    await asyncio.gather(prewarm_task, return_exceptions=True)
                await session.start(call)

            # Send call telemetry (best-effort)
            telemetry = session.get_telemetry() if hasattr(session, "get_telemetry") else None
            if isinstance(telemetry, dict):
                session_tools = call_tools.to_openai_tools() if call_tools else []
                telemetry["toolCount"] = len(session_tools) if session_tools else 0
                telemetry["mcpServerCount"] = len(self._mcp_servers) if self._mcp_servers else 0
                telemetry["builtinTools"] = [t.value for t in self._builtin_tools] if self._builtin_tools else []
                telemetry["recordingEnabled"] = self._recording
                try:
                    await self._control_ws.send(
                        {"event": "call.telemetry", "callId": call.call_id, "telemetry": telemetry}
                    )
                except Exception:
                    pass

            try:
                await media_ws.connect()
            except Exception:
                call.metrics.record_end_reason("error")
            finally:
                # Determine end reason
                if not call.metrics.end_reason:
                    if call.status == "ended":
                        call.metrics.record_end_reason("user_hangup")
                    else:
                        call.metrics.record_end_reason("agent_hangup")

                # Send call metrics (best-effort)
                try:
                    await self._control_ws.send(
                        {"event": "call.metrics", "callId": call.call_id, "metrics": call.metrics.to_dict()}
                    )
                except Exception:
                    pass

                await session.stop()
                if mcp_clients:
                    call_tools.clear_mcp_tools()
                    for c in mcp_clients:
                        await c.close()
                if recorder:
                    recorder.stop()
                # 서버가 확정한 통화 시간을 call_end 핸들러가 읽을 수 있게 잠깐 기다린다.
                # 서버는 미디어 WS 를 먼저 닫고 자원 정리 뒤에 control 종료 프레임을 보내므로
                # (call-handler.js 의 STAGE 순서), 안 기다리면 ended_duration 은 인바운드
                # 사용자에게 **영영 None** 이다 — call_end 가 그들의 유일한 통로다.
                #
                # 종료 프레임 자체는 예전부터 늘 오던 것이라(내용만 바뀐다) 구·신 서버 모두
                # 밀리초 안에 풀린다. 제어 연결이 죽어 안 오는 경우만 상한을 다 쓴다.
                await self._await_server_terminal(call)
                await call._emit("call_end")
                call._mark_ended()
                self._active_sessions.pop(call.call_id, None)
                await self._close_session(call.call_id)
                self._prewarm_attached.discard(call.call_id)

    async def _on_media_start(self, call: CallSession, info: dict[str, Any]) -> None:
        log.info(f"Media stream started: {call.call_id}")

    async def _on_media_stop(self, call: CallSession, session: Session) -> None:
        log.info(f"Media stream stopped: {call.call_id}")
        await session.stop()

    async def _handle_outbound_ready(self, data: dict[str, Any]) -> None:
        call_id = data["callId"]
        media_url = data.get("mediaUrl", "")
        call = self._active_sessions.get(call_id)
        if not call:
            log.warning(f"Unknown outbound call: {call_id}")
            if self._control_ws:
                try:
                    await self._control_ws.send(
                        {
                            "event": "call.session_failed",
                            "callId": call_id,
                            "reason": "UnknownOutboundCall",
                            "message": (
                                "SDK 의 _active_sessions 에 callId 가 없습니다. "
                                "agent.call() 메서드 대신 REST API 를 직접 호출했거나 "
                                "발신 후 SDK 프로세스가 재시작된 경우입니다."
                            ),
                        }
                    )
                except Exception:
                    pass
            return
        call.status = "in-progress"
        log.info(f"Outbound call answered: {call.from_number} -> {call.to_number} ({call_id})")

        # prewarm 은 보통 _handle_ringing(ring 구간)에서 이미 시작됐다. 여기서는
        # ringing 이벤트가 오지 않은 경우의 fallback 으로만 시작한다.
        # _start_call_session 이 prewarm task 를 await 후 attach() 로 부착한다.
        # prewarm_enabled=False 면 기존 start() 경로로 동작.
        self._start_prewarm(call_id)

        asyncio.create_task(self._safe_start_call_session(call, media_url))

    async def _prewarm_session(self, call_id: str) -> None:
        """outbound_ready 시점에 LLM WS 를 미리 연결한다.

        실패/timeout 시 _prewarm_failed 에 등록하여 _start_call_session 이
        기존 start() 경로로 fallback 한다.
        """
        t0 = time.monotonic()
        log.info(f"[PREWARM-T] start call_id={call_id} t={t0:.3f}")
        try:
            # prewarm 은 tool 스키마를 LLM 에 확정 전송한다. 여기서 주입하지 않으면
            # @agent.tool 로 등록한 도구가 통째로 빠진 채 세션이 시작된다 —
            # _start_call_session 의 주입은 answer 이후라 이미 늦다.
            session = await self._open_session(call_id)
            self._inject_session_deps(session, self._tool_registry.fork())
            await asyncio.wait_for(session.prewarm(), timeout=10.0)
            log.info(
                f"[PREWARM-T] done call_id={call_id} elapsed_ms={(time.monotonic() - t0) * 1000:.0f}"
            )
        except Exception as e:
            log.warning(f"[PREWARM-T] failed call_id={call_id} err={e}")
            self._prewarm_failed.add(call_id)

    async def _handle_ringing(self, data: dict[str, Any]) -> None:
        call_id = data.get("callId", "")
        call = self._active_sessions.get(call_id)
        if call:
            call.status = "ringing"
            log.info(f"Outbound call ringing: {call_id}")

            # ring 구간(answer 이전)에 prewarm 을 미리 시작한다 — LLM WS 연결 +
            # greeting 생성을 ring 시간으로 흡수해 answer→first-audio latency 를 줄인다.
            # outbound_ready 에서의 prewarm 시작은 ringing 이 안 온 경우의 fallback.
            if self._prewarm_enabled and call_id not in self._prewarm_tasks:
                self._prewarm_tasks[call_id] = asyncio.create_task(
                    self._prewarm_session(call_id)
                )

    async def _cleanup_prewarm(self, call_id: str) -> None:
        """attach 전에 hangup/실패가 발생한 prewarm 세션을 정리한다 (LLM WS leak 방지).

        prewarm task 를 수거한 뒤, 아직 attach 되지 않았으면 session.stop() 으로
        열린 연결과 receive 루프를 닫는다. 이미 attach 된 통화면 정상 종료 경로
        (_start_call_session finally)가 stop() 을 책임지므로 중복 호출하지 않는다.
        """
        prewarm_task = self._prewarm_tasks.pop(call_id, None)
        attached = call_id in self._prewarm_attached
        self._prewarm_failed.discard(call_id)
        self._prewarm_attached.discard(call_id)
        if prewarm_task is None or attached:
            return
        if not prewarm_task.done():
            prewarm_task.cancel()
        await asyncio.gather(prewarm_task, return_exceptions=True)
        # prewarm 이 이미 연결을 열었으면 task cancel 만으로는 닫히지 않는다 — stop() 필요.
        # 반드시 **이 통화의** 세션이어야 한다. 예전에는 self._session 을 세워서, 실패한
        # 발신 하나를 정리하는 것이 그때 통화 중이던 다른 통화의 세션을 내렸다.
        # 등록이 이미 치워졌으면 닫을 것이 없다 — 여기서 self._session 으로 폴백하면
        # 그 교차 종료가 그대로 되살아난다.
        session = self._call_sessions.get(call_id)
        if session is None:
            return
        try:
            await session.stop()
        except Exception as e:
            log.warning(f"prewarm cleanup stop() failed for {call_id}: {e}")

    async def _handle_failed(self, data: dict[str, Any]) -> None:
        call_id = data.get("callId", "")
        reason = data.get("reason", "failed")
        call = self._active_sessions.get(call_id)
        if call:
            call.status = reason
            call.ended_status = reason
            log.info(f"Outbound call failed: {call_id} ({reason})")
            await call._emit("call_failed", reason)
            call._mark_ended(reason)
            self._active_sessions.pop(call_id, None)
        await self._cleanup_prewarm(call_id)
        await self._close_session(call_id)

    async def _handle_ended(self, data: dict[str, Any]) -> None:
        call_id = data.get("callId", "")
        # 서버는 종료 사유를 status 로 싣는다(completed/no-answer/busy/rejected/canceled/
        # failed). 예전에는 이 값을 버리고 전부 'completed' 로 표시해 상대가 받지 않은
        # 통화를 성사된 통화와 구분할 수 없었다.
        status = data.get("status") or "completed"
        # 서버가 확정한 통화 시간. 구 서버는 이 값을 안 보내거나 0 을 보내므로 그대로 둔다 —
        # 없는 값을 로컬 계산으로 지어내면 어느 쪽인지 구분할 수 없게 된다.
        duration = data.get("duration")
        if call_id in self._screening:
            self._screening[call_id] = True
        call = self._active_sessions.get(call_id)
        log.info(f"Call ended (server): {call_id} (status={status})")
        if call:
            call.status = status
            call.ended_status = status
            if duration is not None:
                call.ended_duration = duration
            if status != "completed":
                # 미연결 종료. call_start 가 없었으므로 call_end 도 발화되지 않는다 —
                # 이 이벤트가 발신 실패를 알 수 있는 유일한 통로다.
                log.info(f"Outbound call not connected: {call_id} ({status})")
                await call._emit("call_failed", status)
            call._mark_ended(status)
        # 미디어 정리를 마치고 이 프레임을 기다리는 통화가 있으면 깨운다(_await_server_terminal).
        waiter = self._terminal_waiters.get(call_id)
        if waiter is not None:
            waiter.set()
        self._active_sessions.pop(call_id, None)
        # 순서가 중요하다 — _cleanup_prewarm 이 이 통화의 세션을 stop() 해야 하므로
        # 등록을 지우기 전에 돌린다.
        await self._cleanup_prewarm(call_id)
        await self._close_session(call_id)

    async def _await_server_terminal(self, call: CallSession) -> None:
        """서버 종료 프레임을 짧게 기다린다. 미디어 정리 직후에만 부른다.

        왜 기다리나: `call_end` 는 인바운드 사용자가 통화 결과를 받는 **유일한 통로**인데,
        서버는 미디어 WS 를 먼저 닫고 자원 정리를 마친 뒤에야 control 종료 프레임을 보낸다.
        안 기다리면 그 핸들러 안에서 `ended_duration` 은 영영 None 이다.

        상한을 다 쓰는 경우는 제어 연결이 죽어 프레임이 아예 안 오는 때뿐이다 — 그 프레임은
        예전부터 늘 오던 것이고(이번에 바뀐 건 내용뿐), 정상 경로에서는 밀리초 안에 풀린다.
        """
        if call.ended_duration is not None:
            return
        # 제어 연결을 이미 내놓았으면(drain/disconnect 이후) 종료 프레임은 영영 오지 않는다.
        # 기다려 봐야 통화마다 유예를 통째로 헛쓰고 drain 꼬리만 길어진다.
        if self._control_given_up:
            return
        waiter = asyncio.Event()
        self._terminal_waiters[call.call_id] = waiter
        try:
            await asyncio.wait_for(waiter.wait(), TERMINAL_FRAME_GRACE_S)
        except asyncio.TimeoutError:
            log.debug(
                "서버 종료 프레임이 %.1f초 안에 오지 않았다 — ended_duration 없이 진행: %s",
                TERMINAL_FRAME_GRACE_S,
                call.call_id,
            )
        finally:
            self._terminal_waiters.pop(call.call_id, None)

    def _on_dtmf_event(self, call: CallSession, digit: str) -> None:
        """DTMF 수신 시 호출. 수집 중이면 collect_dtmf 로, 아니면 모델 자동 전달로 보낸다.

        한 키는 둘 중 한 곳으로만 간다. 예전엔 모든 키를 수집 큐에도 쌓아 두어서, 동의 때 자동
        전달된 ``1`` 이 다음 collect_dtmf 의 첫 자리가 됐다(「1534」).
        """
        asyncio.create_task(call._emit("dtmf", digit))

        if call._dtmf_collector_active:
            call._route_dtmf(digit)
            # 안내 도중 누르면 안내를 끊는다(Twilio <Gather> 와 같다).
            asyncio.create_task(call.clear_audio())
            return

        if self._passive_dtmf_finish_key is not None and digit == self._passive_dtmf_finish_key:
            # 종료키 = 지금까지를 즉시 확정, 키 자신은 값에서 뺀다. 모인 게 없으면 키 자체를
            # 넘긴다 — 「#번을 누르세요」 같은 메뉴가 깨지지 않게.
            self._confirm_passive_dtmf(call, lone_key=digit)
            return

        call._passive_dtmf_buffer.append(digit)
        call._passive_dtmf_confirm_now = lambda: self._confirm_passive_dtmf(call)
        if self._passive_dtmf_max_digits is not None and len(call._passive_dtmf_buffer) >= self._passive_dtmf_max_digits:
            self._confirm_passive_dtmf(call)
            return
        call._passive_dtmf_task = asyncio.create_task(self._flush_passive_dtmf(call))

    def _confirm_passive_dtmf(self, call: CallSession, lone_key: str | None = None) -> None:
        """대기 없이 지금까지 모인 키를 확정해 전달한다.

        대기 중인 flush 는 ``_passive_dtmf_task`` 가 바뀐 걸 보고 스스로 물러난다.
        """
        digits = "".join(call._passive_dtmf_buffer) or (lone_key or "")
        call._passive_dtmf_buffer.clear()
        call._passive_dtmf_task = asyncio.create_task(self._feed_passive_dtmf(call, digits)) if digits else None

    async def _flush_passive_dtmf(self, call: CallSession) -> None:
        """이 통화의 패시브 DTMF 버퍼를 debounce 후 flush 해 LLM 에 주입한다.

        앞선 flush 를 cancel 하지 않고, 깨어난 쪽이 "내가 아직 최신인가"를 확인해 스스로
        물러난다. cancel 방식은 이미 sleep 을 지나 feed_dtmf 안에 들어간 flush 까지 잘라서
        주입을 중간에 끊을 수 있었다. _mark_ended 가 _passive_dtmf_task 를 None 으로
        비우므로 통화가 끝난 뒤 깨어난 flush 도 같은 검사에 걸려 아무것도 하지 않는다.
        """
        await asyncio.sleep(self._passive_dtmf_debounce_ms / 1000)
        if call._passive_dtmf_task is not asyncio.current_task():
            return
        digits = "".join(call._passive_dtmf_buffer)
        call._passive_dtmf_buffer.clear()
        call._passive_dtmf_task = None
        if not digits:
            return
        await self._feed_passive_dtmf(call, digits)

    async def _feed_passive_dtmf(self, call: CallSession, digits: str) -> None:
        # 종료키·자리 수 확정은 대기 없이 연달아 올 수 있다(1#2#). 한 통화 안에선 차례로 보낸다.
        if call._passive_dtmf_feed_lock is None:
            call._passive_dtmf_feed_lock = asyncio.Lock()
        async with call._passive_dtmf_feed_lock:
            # 앞선 주입을 기다리는 사이 통화가 끝났으면 보내지 않는다.
            if call._ended_event.is_set():
                return
            # 값은 카드번호일 수 있다 — 자릿수만 남긴다.
            log.info("키패드 입력 %d자리 자동 전달 (call=%s)", len(digits), call.call_id)
            session_obj = self._call_sessions.get(call.call_id)
            if session_obj:
                await session_obj.feed_dtmf(digits)
