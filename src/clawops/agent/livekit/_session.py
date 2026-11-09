"""LiveKitSession — 유저의 LiveKit AgentSession 을 ClawOps `Session` Protocol 로 감싼다.

`ClawOpsAgent` 는 `Session`(`pipeline/_base.py`) 만 알고 통화를 굴린다. 이 클래스가
그 계약을 구현하면서 내부적으로 LiveKit AgentSession 을 돌린다 — room 없이.

유저는 관용적인 LiveKit 코드를 그대로 쓴다. `Agent` 서브클래스와 `AgentSession`
설정은 손대지 않아도 되고, 유일한 차이는 `session.start(room=...)` 을 우리가
대신 부른다는 것이다 (room 없이).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Awaitable, Callable, Tuple

from livekit.agents import Agent, AgentSession
from livekit.agents.llm import function_tool
from livekit.agents.llm.tool_context import get_fnc_tool_names
from livekit.agents.utils import http_context, is_given
from livekit.agents.voice.io import TextOutput
from livekit.agents.voice.transcription import TranscriptSynchronizer

from .._session import CallSession
from .._tool import ToolRegistry
from ..media import MediaProfile
from ..pipeline._buffering_call import _BufferingCall, drain_into
from ._io import ClawOpsAudioInput, ClawOpsAudioOutput
from ._toolset import ClawOpsPhoneTools

log = logging.getLogger("clawops.agent.livekit")

CreateFn = Callable[["CallSession | None"], Awaitable[Tuple[AgentSession[None], Agent]]]
"""통화당 1회 호출되는 팩토리. (AgentSession, Agent) 를 반환한다.

`call` 은 착신에서는 실제 CallSession, 발신 prewarm 중에는 None 이다
(prewarm 은 응답 전에 도는데 `Session.prewarm()` 이 call 을 넘겨주지 않는다).
"""


class LiveKitSession:
    """LiveKit AgentSession 을 ClawOps 전화망에 물린다.

        async def create(call):
            session = AgentSession(
                llm=openai.realtime.RealtimeModel(modalities=["text"]),
                tts=cartesia.TTS(model="sonic-3.5", language="ko"),
            )
            return session, MyAgent()

        agent = ClawOpsAgent(
            from_="07012341234",
            session_factory=lambda: LiveKitSession(create),
        )

    동시통화를 받으려면 위처럼 `session_factory=` 로 넘긴다 — 통화마다 이 클래스가 새로
    만들어져 `_session`/`_agent`/`_target` 이 통화 안에 갇힌다. `session=` 으로 인스턴스를
    직접 넘기면 통화 간에 그 객체를 공유하므로 동시통화 1건까지다.
    """

    def __init__(
        self, create: CreateFn, *, audio_pause: bool = False,
        media_profile: MediaProfile = MediaProfile.MULAW_8000,
    ) -> None:
        self._media_profile = MediaProfile(media_profile)
        self._create = create
        # 오디오 출력의 일시정지 능력(`_io.py` 모듈 docstring 「일시정지」). 켜면 LiveKit 이
        # 오탐 끼어들기(잡음·기침)로 멈춘 안내를 `false_interruption_timeout` 뒤 되살린다.
        # 끼어들기 동작이 달라지므로 기본은 꺼 둔다 — 호출자가 명시적으로 켠다.
        self._audio_pause = audio_pause

        self._session: AgentSession[None] | None = None
        self._agent: Agent | None = None
        self._input: ClawOpsAudioInput | None = None
        self._output: ClawOpsAudioOutput | None = None
        self._playout_gate: asyncio.Event | None = None
        self._toolset: ClawOpsPhoneTools | None = None
        self._target: Any = None  # CallSession | _BufferingCall
        self._user_tools: list[Any] = []  # 유저가 준 원본 도구 (우리 것 추가 전 스냅샷)

        self._builtin_tools: set[Any] | None = None
        self._tool_registry: ToolRegistry | None = None
        self._http_stack: contextlib.AsyncExitStack | None = None
        # transcript emit 은 LiveKit 의 동기 이벤트 콜백에서 fire-and-forget 로 돈다.
        # GC 로 태스크가 사라지지 않게 참조를 잡아둔다.
        self._emit_tasks: set[asyncio.Task[None]] = set()
        # 마지막으로 띄운 emit 태스크 — 다음 emit 은 이게 끝난 뒤에 핸들러를 부른다(_forward_event).
        self._emit_tail: asyncio.Task[None] | None = None

    def set_playout_gate(self, gate: asyncio.Event | None) -> None:
        """엔진이 보관 오디오를 내보내기 시작하는 순간 set 되는 이벤트를 단다.

        관리형 발신처럼 상대가 받기 전에 인사를 만들어 두는 경우, 받기 전까지는 재생 완료 대기
        시간을 세지 않는다(``ClawOpsAudioOutput.set_playout_gate``). 세션을 열기 전에 부른다.
        """
        self._playout_gate = gate
        if self._output is not None:
            self._output.set_playout_gate(gate)

    # ── ClawOpsAgent 가 duck-typing 으로 부르는 setter ──────────

    def set_builtin_tools(self, tools: set[Any]) -> None:
        self._builtin_tools = tools

    def set_tool_registry(self, registry: ToolRegistry) -> None:
        self._tool_registry = registry

    # ── 프로세스 수명 훅 (ClawOpsAgent.connect/disconnect 에서 root task 로 호출) ──

    async def session_setup(self) -> None:
        """HTTP 기반 LiveKit 플러그인(Cartesia/Deepgram 등)을 위한 준비.

        이들은 워커의 공유 aiohttp 세션(`http_context`)을 통해 연결한다. room-less
        로는 워커가 없으므로 우리가 열어줘야 한다. 이 훅은 control WS task 와 모든
        통화 task 의 공통 조상인 root task 에서 실행되므로, 여기서 연 컨텍스트를
        하위 TTS/STT task 들이 상속받는다. (이미 열려 있으면 open() 이 no-op.)
        """
        if self._http_stack is not None:
            return
        # 컨텍스트 진입에 성공한 뒤에야 self 에 저장한다 — 실패 시 _http_stack 이
        # 빈 채로 남으면 이후 session_setup 이 계속 early-return 해 http_context 가
        # 영영 안 열린다(무음 통화).
        stack = contextlib.AsyncExitStack()
        await stack.enter_async_context(http_context.open())
        self._http_stack = stack

    async def session_teardown(self) -> None:
        if self._http_stack is not None:
            await self._http_stack.aclose()
            self._http_stack = None

    # ── Session Protocol ────────────────────────────────────────

    async def start(self, call: CallSession) -> None:
        await self._boot(call)

    async def prewarm(self) -> None:
        await self._boot(None)

    async def attach(self, call: CallSession) -> None:
        # boot 성공 신호는 _session (부분 완료된 boot 은 _output 만 set 돼 있을 수 있다).
        if self._session is None or self._output is None or self._agent is None:
            # prewarm 이 실패했거나 건너뛴 경로 — 그냥 새로 띄운다.
            await self._boot(call)
            return

        prev = self._target
        self._output.set_call(call)
        self._target = call
        # ⚠️ 아웃바운드 prewarm 은 _boot(None) 이 setter 실행 전에 돌아 도구를
        # 비운 채 시작한다. setter 는 이제 실행됐으므로 여기서 도구를 다시 붙인다.
        await self._apply_tools(call)
        await drain_into(prev, call)

    async def feed_audio(self, audio: bytes, timestamp: int) -> None:
        if self._input is not None:
            self._input.push_audio(audio)

    async def feed_dtmf(self, digits: str) -> None:
        if self._session is None:
            return
        # generate_reply 는 SpeechHandle 을 반환하는 동기 메서드다. teardown 중에는
        # 세션 activity 가 이미 정리돼 RuntimeError 를 던지므로 삼킨다.
        try:
            self._session.generate_reply(user_input=f"[전화 키패드 입력] {digits}")
        except RuntimeError as e:
            log.debug(f"feed_dtmf skipped (session not running): {e}")

    async def stop(self) -> None:
        if self._input is not None:
            self._input.end_input()
        await self._close_agent_session()
        # 게이트는 통화 하나의 것이다 — 이 객체가 다음 통화에 다시 쓰여도 넘어가지 않게 지운다.
        self._playout_gate = None

    async def _close_agent_session(self) -> None:
        """현재 AgentSession + 출력 태스크를 정리한다.

        stop() 과 _boot() 재진입(attach 실패 폴백)이 공통으로 부른다 — 후자에서
        기존 세션을 닫지 않으면 LLM WS 가 새고 두 세션이 같은 통화에 오디오를 보낸다.
        """
        if self._output is not None:
            self._output.close()
            self._output = None
        if self._session is not None:
            try:
                await self._session.aclose()
            except Exception as e:
                log.warning(f"AgentSession.aclose() failed: {e}")
            self._session = None

    def get_telemetry(self) -> dict[str, Any] | None:
        # TODO: conversation_item_added(ChatMessage.metrics.e2e_latency / .interrupted) 와
        # function_tools_executed 로 CallMetrics 를 채운다.
        # metrics_collected 는 1.6.5 에서 deprecated 이므로 쓰지 말 것.
        return None

    # ── 내부 ────────────────────────────────────────────────────

    async def _boot(self, call: CallSession | None) -> None:
        # 재진입(attach 실패 폴백) 시 이전 세션이 새지 않도록 먼저 닫는다.
        await self._close_agent_session()

        target: Any = call if call is not None else _BufferingCall()

        session, agent = await self._create(call)
        _validate(session, agent)

        self._input = ClawOpsAudioInput(media_profile=self._media_profile)
        self._output = ClawOpsAudioOutput(target, pause=self._audio_pause, media_profile=self._media_profile)
        self._output.set_playout_gate(self._playout_gate)

        # ⚠️ TranscriptSynchronizer 는 필수다. room 없이는 이걸 안 씌우면
        # barge-in 시 synchronized_transcript 가 None 이라 LLM 컨텍스트에
        # "말하지 않은 전체 텍스트"가 기록된다. _io.py 계약 1 참조.
        # 동기화기가 재생 속도에 맞춰 내보내는 텍스트를 `transcript_delta` 훅으로 흘린다.
        # 종전엔 next_in_chain_text=None 이라 그대로 버려졌다. _TranscriptDeltaOutput 참조.
        sync = TranscriptSynchronizer(
            next_in_chain_audio=self._output,
            next_in_chain_text=_TranscriptDeltaOutput(
                lambda text: self._forward_event("transcript_delta", "assistant", text)
            ),
        )
        session.input.audio = self._input
        session.output.audio = sync.audio_output
        session.output.transcription = sync.text_output

        self._wire_transcripts(session)

        self._agent = agent
        # 우리 도구를 붙이기 전 유저 원본 도구를 스냅샷한다 (attach 재적용 시 누적 방지).
        self._user_tools = list(agent.tools)
        await self._apply_tools(call)

        # room= 을 넘기지 않는 것이 이 통합의 전부다.
        await session.start(agent)

        self._session = session
        self._target = target

    async def _apply_tools(self, call: CallSession | None) -> None:
        """유저 도구 + 브리지된 registry 도구 + 내장 전화 도구를 이름 충돌 없이 붙인다.

        아웃바운드 prewarm(_boot(None))은 setter 실행 전에 돌아 registry/builtin 이
        비어 있고, 그 사이엔 통화가 연결되지 않아 도구를 쓸 일도 없다. 그래서 이때는
        유저 도구만 올리고, attach() 가 setter 실행 후 실제 도구를 붙이는 유일한
        조립 지점이 된다.
        """
        if self._agent is None:
            return

        if call is None:  # prewarm — 아직 registry/builtin 이 없다
            await self._agent.update_tools(list(self._user_tools))
            return

        # 유저 도구가 Toolset 이면 .id 는 묶음 이름이라 멤버 도구 이름을 못 잡는다.
        # get_fnc_tool_names 로 Toolset 을 풀어 실제 함수 이름을 모은다 — 안 그러면
        # 내장 도구와 겹칠 때 flatten 이 "duplicate function name" 으로 통화를 떨군다.
        taken = set(get_fnc_tool_names(self._user_tools))
        bridged_all = _bridge_registry(self._tool_registry)
        bridged: list[Any] = []
        for tool, name in zip(bridged_all, get_fnc_tool_names(bridged_all)):
            if name in taken:
                continue  # 유저 도구가 우선 — 이름 충돌 시 건너뛴다
            taken.add(name)
            bridged.append(tool)

        # 내장 전화 도구 중 이름이 겹치는 것은 제외한다 (duplicate function name 방지).
        self._toolset = ClawOpsPhoneTools(enabled=self._builtin_tools, exclude_names=taken)
        self._toolset.set_call(call)

        await self._agent.update_tools([*self._user_tools, *bridged, self._toolset])

    def _wire_transcripts(self, session: AgentSession[None]) -> None:
        """LiveKit 의 최종 대화 항목을 ClawOps `transcript` 훅으로 흘려보낸다.

        네이티브 세션(OpenAI/Gemini/Pipeline)은 `call._emit("transcript", role, text)`
        를 부른다. `@agent.on("transcript")` 로 트랜스크립트를 모으는 기존 앱이 세션만
        바꿔도 그대로 돌게 하려면 여기서 같은 계약을 재현해야 한다.

        `conversation_item_added` 는 user·assistant 최종 메시지 양쪽에 대해 히스토리에
        커밋될 때 한 번씩 뜬다 — 부분(interim) transcript 중복 없이 최종만 얻는다.
        item 이 AgentHandoff 등 ChatMessage 가 아니면 role 이 없어 자연히 걸러진다.
        """

        def _on_item(ev: Any) -> None:
            item = getattr(ev, "item", None)
            role = getattr(item, "role", None)
            if role not in ("user", "assistant"):
                return
            text = getattr(item, "text_content", None)
            if not text:
                return
            self._forward_transcript(role, text)

        session.on("conversation_item_added", _on_item)

        last_user_text = ""

        def _on_user_transcribed(ev: Any) -> None:
            # 발신자 중간 전사 → `transcript_delta`. 확정(is_final)은 위 대화 항목이 `transcript`
            # 로 내보내므로 여기서는 건너뛴다. LiveKit 의 중간 전사는 그 발화의 누적 문장이라
            # 앞부분이 고쳐 쓰일 수 있다 — 그대로 넘긴다.
            # realtime 은 같은 중간 전사를 연달아 두 번 내기도 한다(실측) — 바로 앞과 같으면 건너뛴다.
            nonlocal last_user_text
            if getattr(ev, "is_final", True):
                last_user_text = ""
                return
            text = getattr(ev, "transcript", "") or ""
            if not text.strip() or text == last_user_text:
                return
            last_user_text = text
            self._forward_event("transcript_delta", "user", text)

        session.on("user_input_transcribed", _on_user_transcribed)

    def _forward_transcript(self, role: str, text: str) -> None:
        self._forward_event("transcript", role, text)

    def _forward_event(self, event: str, *args: Any) -> None:
        # 동기 콜백에서 async _emit 을 태워야 하므로 태스크로 띄운다. prewarm 중이면
        # _target 이 _BufferingCall 이라 _emit 이 드롭 카운트로 흡수한다 (무해).
        #
        # ⚠️ 태스크는 **앞 태스크가 끝난 뒤** 핸들러를 부른다. 따로 띄우기만 하면 핸들러가 await 하는
        # 동안 뒤 이벤트가 먼저 처리돼, 더 짧은 옛 누적이나 확정 뒤의 transcript_delta 가 앱에 늦게
        # 닿는다 — 「받을 때마다 바꿔 끼운다」는 transcript_delta 계약이 깨진다.
        target = self._target
        if target is None:
            return
        task = asyncio.create_task(self._emit_after(self._emit_tail, target, event, args))
        self._emit_tail = task
        self._emit_tasks.add(task)
        task.add_done_callback(self._emit_tasks.discard)

    @staticmethod
    async def _emit_after(
        prev: asyncio.Task[None] | None, target: Any, event: str, args: Tuple[Any, ...]
    ) -> None:
        if prev is not None and not prev.done():
            # wait 는 앞 태스크의 예외·취소를 다시 던지지 않는다 — 앞 핸들러가 죽어도 뒤는 간다.
            await asyncio.wait([prev])
        await target._emit(event, *args)


class _TranscriptDeltaOutput(TextOutput):
    """동기화기 다음 칸 — 재생에 맞춰 나오는 에이전트 텍스트를 **누적**해 콜백으로 넘긴다.

    `TranscriptSynchronizer` 는 오디오 재생 위치에 맞춰 텍스트를 조각으로 흘린다. 조각만 넘기면
    받는 쪽이 이어 붙여야 하고 한 조각을 놓치면 어긋나므로, 세그먼트(한 번의 발화) 안에서 누적한
    문장을 매번 넘긴다. `flush()` 가 세그먼트 끝이다 — 누적을 비운다.

    ⚠️ 끊긴 발화는 마지막 누적이 실제 재생보다 한 단어쯤 앞설 수 있다(동기화기가 단어를 재생 직전에
    내보낸다). 재생된 데까지 자른 확정본은 `transcript` 훅이 따로 준다.
    """

    def __init__(self, on_text: Callable[[str], None]) -> None:
        super().__init__(label="ClawOpsTranscriptDelta", next_in_chain=None)
        self._on_text = on_text
        self._text = ""

    async def capture_text(self, text: str) -> None:
        if not text:
            return
        self._text += text
        self._on_text(self._text)

    def flush(self) -> None:
        self._text = ""


def _resolve(session: AgentSession[None], agent: Agent, attr: str) -> Any:
    """LiveKit 의 우선순위(Agent 가 Session 을 덮어씀)를 따라 컴포넌트를 찾는다."""
    value = getattr(agent, attr, None)
    if value is not None and is_given(value):
        return value
    return getattr(session, attr, None)


def _validate(session: AgentSession[None], agent: Agent) -> None:
    """LiveKit 이 조용히 넘어가는 두 조합을 우리가 막는다.

    LiveKit 은 `agent_activity.py` 에서 logger.error 만 찍고 진행하므로,
    실수하면 "말을 못 하는 에이전트"가 아무 예외 없이 배포된다.
    """
    llm = _resolve(session, agent, "llm")
    tts = _resolve(session, agent, "tts")

    caps = getattr(llm, "capabilities", None)
    audio_output = getattr(caps, "audio_output", None)
    if audio_output is None:
        return  # realtime 모델이 아니다 (일반 LLM + TTS 파이프라인)

    if not audio_output and tts is None:
        raise ValueError(
            "RealtimeModel 이 modalities=['text'] 인데 tts 가 없습니다 — "
            "에이전트가 소리를 내지 못합니다. AgentSession(tts=...) 를 주거나 "
            "modalities 에 'audio' 를 넣으세요."
        )
    if audio_output and tts is not None:
        log.warning(
            "RealtimeModel 이 오디오를 직접 출력하므로 tts 가 무시됩니다. "
            "TTS 를 쓰려면 RealtimeModel(modalities=['text']) 로 설정하세요."
        )


def _bridge_registry(registry: ToolRegistry | None) -> list[Any]:
    """`@agent.tool` / MCP 도구를 LiveKit function_tool 로 노출한다.

    LiveKit 경로에서는 보통 LiveKit 의 `@function_tool` 을 쓰지만, 기존 ClawOps
    데코레이터로 등록한 도구도 그대로 동작해야 한다.
    """
    if registry is None:
        return []

    tools: list[Any] = []
    for spec in registry.to_openai_tools():
        # ClawOps 는 realtime 포맷(name/parameters 가 최상위)으로 낸다.
        # MCP 도구 등 name 이 다른 위치에 있는 스키마는 방어적으로 건너뛴다.
        name = spec.get("name")
        if not name or "parameters" not in spec:
            continue
        raw_schema = {
            "name": name,
            "description": spec.get("description", ""),
            "parameters": spec["parameters"],
        }
        tools.append(
            function_tool(_make_raw_handler(registry, name), raw_schema=raw_schema)
        )
    return tools


def _make_raw_handler(registry: ToolRegistry, name: str) -> Callable[..., Awaitable[str]]:
    # raw function tool 의 인자 이름은 반드시 `raw_arguments` 여야 한다 (llm/utils.py:559).
    async def handler(raw_arguments: dict[str, object]) -> str:
        return await registry.call(name, dict(raw_arguments))

    handler.__name__ = name
    return handler
