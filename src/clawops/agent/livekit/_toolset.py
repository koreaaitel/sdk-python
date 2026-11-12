"""ClawOps 전화 내장 도구를 LiveKit Toolset 으로 노출한다.

`hang_up` / `collect_dtmf` / `send_dtmf` / `transfer_call` — `_builtin_tool_schemas.py`
의 스키마와 동작을 미러한다. 어느 쪽을 고치든 다른 쪽도 같이 볼 것.

설계 메모 2가지:

1. **`CallSession` 을 `self` 로 들고 간다.** `AgentSession.userdata` 는 제네릭 슬롯이
   하나뿐이라 우리가 차지하면 유저가 못 쓴다. LiveKit 자신의 `EndCallTool` 도 같은
   이유로 `Toolset` 서브클래스에 상태를 얹는다.
2. **LiveKit 이 파는 전화 도구는 못 쓴다.** `beta/tools/end_call.py` 와
   `send_dtmf.py` 는 `ctx.session.room_io.room` / `get_job_context()` 를 잡아서
   room 없이는 죽는다. 패턴만 가져오고 코드는 우리 것을 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from livekit.agents.llm import StopResponse, ToolFlag, Toolset, function_tool
from livekit.agents.voice.events import RunContext

from .._builtin_tools import BuiltinTool
from .._session import CallSession, DtmfCollectorBusy
from .._transfer_tool import TRANSFER_INITIATED, is_call_over, wait_for_transfer_result

log = logging.getLogger("clawops.agent.livekit")

# hang_up 의 대기 상한(호출 시점부터 모든 대기 합계). TTS 가 멈추거나 재생 완료 신호가 안 와도 통화는 끊는다.
HANG_UP_PLAYOUT_CAP_S = 15.0

# 말 없이 hang_up 만 부른 응답에 돌려주는 도구 결과. LiveKit 은 이걸 받고 같은 턴에서 한 번 더 응답한다.
# 에이전트 언어가 제각각이라 지시는 언어 중립으로 두고 「대화 언어로」 말하게 한다.
HANG_UP_FAREWELL_PROMPT = (
    "The call will end now. Say one short goodbye sentence to the caller, in the same language "
    "as this conversation. Do not ask anything, do not start a new topic, and do not call any "
    "tools. The call hangs up as soon as you finish speaking."
)

_HANG_UP_DESCRIPTION = (
    "End the phone call. Use when the conversation is finished or the caller says goodbye."
)

_TRANSFER_DESCRIPTION = (
    "Transfer the current call to another phone number or SIP endpoint. "
    "Use for blind transfer (direct handoff) or warm transfer (with whisper message to the target)."
)


class ClawOpsPhoneTools(Toolset):
    """ClawOps 통화 제어 도구 묶음.

    `builtin_tools` 로 개별 on/off 한다 (`ClawOpsAgent(builtin_tools=...)` 와 동일).
    """

    def __init__(
        self,
        *,
        enabled: set[BuiltinTool] | None = None,
        exclude_names: set[str] | None = None,
    ) -> None:
        self._call: CallSession | None = None
        self._hung_up = False
        self._hangup_after_turn: asyncio.Task[None] | None = None

        # (BuiltinTool, 핸들러, 설명, flags) — tool 이름은 enum.value 와 같다.
        # IGNORE_ON_ENTER: 인사말 도중 모델이 통화를 끊거나 전환하는 사고를 막는다.
        specs: list[tuple[BuiltinTool, Callable[..., Awaitable[str]], str, ToolFlag]] = [
            (BuiltinTool.HANG_UP, self._hang_up, _HANG_UP_DESCRIPTION, ToolFlag.IGNORE_ON_ENTER),
            (
                BuiltinTool.COLLECT_DTMF,
                self._collect_dtmf,
                "사용자로부터 DTMF(전화 키패드) 입력을 수집합니다. "
                "반드시 사용자에게 무엇을 입력해야 하는지 안내한 후 호출하세요.",
                ToolFlag.NONE,
            ),
            (
                BuiltinTool.SEND_DTMF,
                self._send_dtmf,
                "DTMF 신호를 전송합니다. ARS 메뉴 탐색이나 내선번호 입력 시 사용합니다.",
                ToolFlag.NONE,
            ),
            (
                BuiltinTool.TRANSFER_CALL,
                self._transfer_call,
                _TRANSFER_DESCRIPTION,
                ToolFlag.IGNORE_ON_ENTER,
            ),
        ]

        if enabled is None:
            enabled = {tool_enum for tool_enum, *_ in specs}
        # 유저/registry 도구와 이름이 겹치는 내장 도구는 제외한다 — 안 그러면
        # ToolContext.flatten() 이 "duplicate function name" 으로 세션 시작을 막는다.
        exclude_names = exclude_names or set()

        tools: list[Any] = []
        for tool_enum, handler, description, flags in specs:
            name = tool_enum.value  # BuiltinTool.HANG_UP.value == "hang_up"
            if tool_enum not in enabled or name in exclude_names:
                continue
            tools.append(function_tool(handler, name=name, description=description, flags=flags))

        super().__init__(id="clawops_phone", tools=tools)

    def set_call(self, call: CallSession) -> None:
        """prewarm -> attach 시 실제 통화로 교체한다."""
        self._call = call

    def _require_call(self) -> CallSession:
        if self._call is None:
            raise RuntimeError("통화가 아직 연결되지 않았습니다")
        return self._call

    # ── 도구 구현 ───────────────────────────────────────────────

    async def _hang_up(self, ctx: RunContext[None]) -> str:
        """End the phone call.

        작별 인사가 **다 재생된 뒤에** 끊는다. LiveKit 은 도구를 문장 음성 합성·재생과 동시에 돌리므로,
        바로 끊으면 인사가 합성 도중 잘린다. 순서:

        1. 끼어들기를 막는다 — 끊기로 정한 뒤 「네」 같은 맞장구가 인사를 끊으면 LiveKit 이 이 도구를
           취소해 통화가 안 끝난다. 이미 끼어들기가 일어났으면 끊지 않는다(발신자가 말을 이었다).
        2. ``ctx.wait_for_playout()`` 을 기다린다 — 같은 응답에서 먼저 말한 인사.
        3. 이 단계에서 말한 것이 있으면 끊고 ``StopResponse`` — 결과를 돌려주면 LiveKit 이 닫히는 회선에
           대고 응답을 또 만든다.
        4. 파이프라인(STT·LLM·TTS)에서 말 없이 ``hang_up`` 만 부른 응답이면 끊지 않고
           :data:`HANG_UP_FAREWELL_PROMPT` 를 돌려준다. LiveKit 은 같은 턴에서 한 번 더 응답하고, 그 턴
           전체의 재생이 끝나면 뒤에서 끊는다. realtime 모델은 말한 내용이 음성 전사에서 와 판정이
           틀릴 수 있어(인사를 두 번 시킨다) 이 단계를 건너뛴다.

        모든 대기는 호출 시점부터 **합쳐서** :data:`HANG_UP_PLAYOUT_CAP_S` 안이다.
        """
        call = self._require_call()
        if ctx is not None:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + HANG_UP_PLAYOUT_CAP_S
            speech = getattr(ctx, "speech_handle", None)
            # 이 단계의 assistant 메시지는 재생이 끝나야 들어온다 — 지금 개수가 이 단계의 시작점이다.
            items_before = len(getattr(speech, "chat_items", None) or [])
            try:
                ctx.disallow_interruptions()
            except RuntimeError:
                return "통화를 끊지 않았습니다. 발신자가 말을 이었습니다."
            await _wait_until(ctx.wait_for_playout(), deadline, "작별 인사 재생")
            if (
                self._hangup_after_turn is None
                and speech is not None
                and loop.time() < deadline
                and not _is_realtime_session(ctx)
                and not _spoke_since(speech, items_before)
            ):
                # ⚠️ 이 도구의 태스크 안에서 speech.wait_for_playout() 을 부르면 LiveKit 이 순환 대기로
                #    막는다. 새로 띄운 태스크는 도구 태스크 표식을 물려받지 않는다. 세션이 닫히면 speech 도
                #    끝나 이 태스크는 곧바로 끝난다.
                self._hangup_after_turn = asyncio.ensure_future(
                    self._hang_up_after(speech, call, deadline)
                )
                return HANG_UP_FAREWELL_PROMPT
        await self._hang_up_once(call)
        raise StopResponse()

    async def _hang_up_after(self, speech: Any, call: CallSession, deadline: float) -> None:
        await _wait_until(speech.wait_for_playout(), deadline, "작별 인사 턴")
        try:
            await self._hang_up_once(call)
        except Exception as e:  # noqa: BLE001 — 인사 도중 상대가 먼저 끊었으면 닫힌 회선이다
            log.info(f"hang_up: 이미 끝난 통화라 끊기 생략: {e}")

    async def _hang_up_once(self, call: CallSession) -> None:
        if self._hung_up:
            return
        self._hung_up = True
        await call.hangup()

    async def _collect_dtmf(
        self,
        ctx: RunContext[None],
        max_digits: int,
        finish_on_key: str = "#",
        timeout: int = 5,
    ) -> str:
        """DTMF 입력을 수집한다.

        Args:
            max_digits: 수집할 최대 자릿수
            finish_on_key: 입력 종료 키 (기본: #)
            timeout: 입력 대기 시간(초, 기본: 5)
        """
        try:
            result = await self._require_call().collect_dtmf(
                max_digits=max_digits,
                finish_on_key=finish_on_key,
                timeout=timeout,
            )
            return result if result else "(타임아웃 - 입력 없음)"
        except DtmfCollectorBusy:
            # 고장이 아니라 중복 호출이다 — "Error" 로 돌려주면 모델이 도구가 망가진 줄 알고
            # 다시 부르지 않고, 그때부터 발신자가 누르는 키는 아무도 받지 않는다.
            return "(이미 입력을 받는 중입니다. 결과를 기다리세요.)"
        except Exception as e:
            return f"Error: {e}"

    async def _send_dtmf(self, ctx: RunContext[None], digits: str) -> str:
        """DTMF 신호를 전송한다.

        Args:
            digits: 전송할 번호 (0-9, *, #). 'w'는 500ms 대기, 'W'는 1000ms 대기. 예: '1234#'
        """
        try:
            await self._require_call().send_dtmf_sequence(digits)
            return "sent"
        except Exception as e:
            return f"Error: {e}"

    async def _transfer_call(
        self,
        ctx: RunContext[None],
        to: str,
        destination_type: str = "pstn",
        mode: str = "blind",
        after_transfer: str = "terminate",
        whisper: str | None = None,
        caller_id_mode: str | None = None,
        caller_id: str | None = None,
        timeout: int = 30,
    ) -> str:
        """통화를 다른 번호나 SIP 엔드포인트로 전환한다.

        Args:
            to: 전환 대상. destination_type 이 'pstn' 이면 전화번호, 'sip' 이면 SIP URI.
            destination_type: pstn(기본, 통신사 경유) 또는 sip(SIP 직결)
            mode: blind(기본, 즉시 전환) 또는 warm(대상에게 whisper 후 연결)
            after_transfer: terminate(기본, AI 세션 종료) 또는 return(전환 종료 후 AI 복귀,
                이 도구가 전환 결과를 돌려준다)
            whisper: warm 모드에서 대상에게 먼저 들려줄 안내 문구
            caller_id_mode: 전환받는 쪽에 표시할 번호를 **의도**로 지정.
                account(기본과 같음, 계정 번호) 또는 original(걸려온 통화의 발신자
                번호를 표시하려는 선호 — 표시할 수 없는 통화면 계정 번호로 내려앉고
                전환은 성사된다).
            caller_id: 표시할 번호를 **직접** 지정. 계정 보유번호이거나 그 통화의
                발신자여야 하고, 벗어나면 전환 자체가 실패한다. 웬만하면
                caller_id_mode 를 쓴다. 둘 다 주면 caller_id 가 이긴다.
            timeout: 전환 대상 응답 대기 시간(초)
        """
        call = self._require_call()

        # 인자 검증을 ensure_future 앞에서 한다.
        # (_builtin_tool_schemas.py:194 는 ensure_future 를 try/except 로 감쌌는데,
        #  ensure_future 는 즉시 반환하므로 인자 오류가 except 를 지나 future 안에서
        #  터진다. 그러면 LLM 은 transfer_initiated 를 받고 발신자에게 "전환합니다"
        #  라고 말하지만 실제로는 아무 일도 일어나지 않는다.)
        if destination_type not in ("pstn", "sip"):
            return f"Error: destination_type must be 'pstn' or 'sip', got {destination_type!r}"
        if mode not in ("blind", "warm"):
            return f"Error: mode must be 'blind' or 'warm', got {mode!r}"
        if after_transfer not in ("terminate", "return"):
            return f"Error: after_transfer must be 'terminate' or 'return', got {after_transfer!r}"
        if caller_id_mode is not None and caller_id_mode not in ("account", "original"):
            return (
                f"Error: caller_id_mode must be 'account' or 'original', got {caller_id_mode!r}"
            )
        if not to.strip():
            return "Error: 'to' must not be empty"

        # 결과를 기다리는 도구가 끼어들기로 취소되면 이어받을 때 할 말이 사라진다(LiveKit 은 끼어들기가
        # 일어나면 실행 중인 도구를 취소한다). 도구가 끝나면 되돌린다 — 이어받는 발화는 끊을 수 있어야 한다.
        returns = after_transfer == "return"
        speech = ctx.speech_handle if returns and ctx is not None else None
        allowed_before = speech.allow_interruptions if speech is not None else False
        if speech is not None:
            try:
                ctx.disallow_interruptions()
            except RuntimeError:
                return "Error: 이미 끼어들기가 일어나 전환하지 않았습니다"

        coro = call.transfer(
            to=to,
            destination_type=destination_type,  # type: ignore[arg-type]
            mode=mode,
            after_transfer=after_transfer,
            whisper=whisper,
            caller_id=caller_id,
            caller_id_mode=caller_id_mode,  # type: ignore[arg-type]
            timeout=timeout,
        )

        task = asyncio.ensure_future(coro)
        # 실패가 도구 결과로 나가지 않는 경로(terminate·취소된 도구)에서도 로그에는 남긴다.
        task.add_done_callback(_log_transfer_result)
        # terminate 는 서버가 미디어 세션을 닫으므로 기다리면 세션이 먼저 끝난다. 즉시 반환한다.
        if not returns:
            return TRANSFER_INITIATED
        lost_fn = call._transfer_connection_lost_fn
        try:
            result = await wait_for_transfer_result(
                task,
                call_ended=lambda: call.ended_status is not None,
                connection_lost=lost_fn() if lost_fn is not None else None,
            )
        except Exception as e:  # noqa: BLE001 — 도구는 문자열로 실패를 알린다
            return f"Error: {e}"
        finally:
            if allowed_before:
                speech.allow_interruptions = True
        # 통화가 끝났다 — 들을 사람이 없으니 모델 응답을 만들지 않는다.
        if is_call_over(result):
            raise StopResponse()
        return result


async def _wait_until(aw: Awaitable[Any], deadline: float, what: str) -> None:
    """``deadline``(loop 시각)까지 기다린다. 넘거나 실패해도 던지지 않는다 — 끊기는 해야 한다."""
    try:
        await asyncio.wait_for(aw, max(0.0, deadline - asyncio.get_running_loop().time()))
    except asyncio.TimeoutError:
        log.warning(f"hang_up: {what}이 {HANG_UP_PLAYOUT_CAP_S:.0f}초 상한 안에 끝나지 않아 그대로 끊는다")
    except Exception as e:  # noqa: BLE001
        log.warning(f"hang_up: {what} 대기 실패, 그대로 끊는다: {e}")


def _spoke_since(speech: Any, start: int) -> bool:
    """``speech.chat_items[start:]`` 에 에이전트가 실제로 말한 문장이 있나.

    LiveKit 은 한 단계의 assistant 메시지를 그 단계의 재생이 끝날 때 넣는다(도구는 그보다 먼저 시작된다).
    도구 진입 시점의 개수부터 보면 **이 도구를 부른 단계**에서 한 말만 센다 — 같은 턴 앞 단계의
    「확인해 볼게요」 같은 말을 작별 인사로 치지 않는다.
    """
    for item in (getattr(speech, "chat_items", None) or [])[start:]:
        if getattr(item, "type", None) == "message" and getattr(item, "role", None) == "assistant":
            if (getattr(item, "text_content", None) or "").strip():
                return True
    return False


def _is_realtime_session(ctx: Any) -> bool:
    """realtime 모델(음성을 모델이 직접 낸다) 세션인가."""
    from livekit.agents.llm import RealtimeModel

    return isinstance(getattr(getattr(ctx, "session", None), "llm", None), RealtimeModel)


def _log_transfer_result(task: Any) -> None:
    """전환 태스크의 예외가 조용히 사라지지 않게 한다."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(f"transfer failed: {exc}")
