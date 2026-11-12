"""실제 LiveKit AgentSession 에서 내장 hang_up 이 작별 인사 **재생 완료 뒤에** 끊는지.

LLM·TTS 노드와 오디오 출력만 가짜로 바꾸고 세션·도구 실행·재생 추적은 LiveKit 그대로 돈다. LLM 은
한 응답에 「작별 인사 + hang_up」을 내고, TTS 는 첫 프레임까지 0.3초 걸리며, 출력은 받은 오디오 길이만큼
실제 시간을 들여 재생한다. 바로 끊던 종전 구현이면 끊는 시점에 재생된 오디오가 없다.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("livekit.agents", reason="clawops[livekit] 미설치")

from livekit import rtc  # noqa: E402
from livekit.agents import Agent, AgentSession, llm, tts  # noqa: E402
from livekit.agents.llm import ChatChunk, ChoiceDelta, FunctionToolCall  # noqa: E402
from livekit.agents.voice.io import AudioOutput, AudioOutputCapabilities  # noqa: E402

from clawops.agent._session import CallSession  # noqa: E402
from clawops.agent.livekit._toolset import ClawOpsPhoneTools  # noqa: E402

SR = 16000
FAREWELL_S = 0.8


class _UnusedLLM(llm.LLM):
    """파이프라인 경로를 켜는 표지 — 노드를 덮어써 호출되지 않는다."""

    def chat(self, **kwargs):  # noqa: ANN003, ANN201
        raise AssertionError("llm_node 를 덮어썼으니 불리지 않는다")


class _UnusedTTS(tts.TTS):
    def __init__(self) -> None:
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False), sample_rate=SR, num_channels=1)

    def synthesize(self, text, **kwargs):  # noqa: ANN001, ANN003, ANN201
        raise AssertionError("tts_node 를 덮어썼으니 불리지 않는다")


class _PlayingOutput(AudioOutput):
    """받은 오디오를 실제 시간만큼 들여 「재생」하는 출력."""

    def __init__(self, timeline: list[str]) -> None:
        super().__init__(label="fake", capabilities=AudioOutputCapabilities(pause=False), sample_rate=SR)
        self._timeline = timeline
        self._pending = 0.0
        self._tasks: list[asyncio.Task] = []

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        await super().capture_frame(frame)
        if self._pending == 0.0:
            self.on_playback_started(created_at=asyncio.get_running_loop().time())
        self._pending += frame.duration

    def flush(self) -> None:
        super().flush()
        dur, self._pending = self._pending, 0.0

        async def _play() -> None:
            await asyncio.sleep(dur)
            self._timeline.append("played")
            self.on_playback_finished(playback_position=dur, interrupted=False)

        self._tasks.append(asyncio.ensure_future(_play()))

    def clear_buffer(self) -> None:
        pass


class _FarewellAgent(Agent):
    def __init__(self, tools: list) -> None:
        super().__init__(instructions="test", tools=tools)
        self.turns = 0

    async def llm_node(self, chat_ctx, tools, model_settings):  # noqa: ANN001, ANN201
        self.turns += 1
        yield "감사합니다. 좋은 하루 보내세요."
        yield ChatChunk(
            id="c1",
            delta=ChoiceDelta(
                role="assistant",
                tool_calls=[FunctionToolCall(name="hang_up", arguments="{}", call_id="call_1")],
            ),
        )

    async def tts_node(self, text, model_settings):  # noqa: ANN001, ANN201
        async for _ in text:
            pass
        await asyncio.sleep(0.3)  # 합성 지연 — 도구는 이 사이에 이미 돈다
        n = int(SR * FAREWELL_S)
        yield rtc.AudioFrame(b"\x00\x00" * n, SR, 1, n)


async def test_hang_up_happens_after_farewell_is_played() -> None:
    timeline: list[str] = []
    call = CallSession(call_id="c", from_number="010", to_number="070", account_id="AC")
    call._hangup_fn = AsyncMock(side_effect=lambda: timeline.append("hangup"))
    phone = ClawOpsPhoneTools()
    phone.set_call(call)

    agent = _FarewellAgent([phone])
    session = AgentSession(llm=_UnusedLLM(), tts=_UnusedTTS())
    session.output.audio = _PlayingOutput(timeline)
    await session.start(agent)
    try:
        session.generate_reply(user_input="네 끊을게요")
        for _ in range(100):
            if "hangup" in timeline:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)  # 끊은 뒤 응답이 더 생기는지 볼 틈
    finally:
        await session.aclose()

    assert "hangup" in timeline, timeline
    assert timeline.index("played") < timeline.index("hangup"), timeline  # 인사 재생이 끝난 뒤에 끊는다
    assert agent.turns == 1  # 끊은 뒤 LLM 을 또 부르지 않는다(StopResponse)
