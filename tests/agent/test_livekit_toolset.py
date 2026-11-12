"""ClawOpsPhoneTools + LiveKitSession 검증 가드 테스트."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("livekit.agents", reason="clawops[livekit] 미설치")

from clawops.agent._session import CallSession  # noqa: E402
from clawops.agent.livekit._session import _validate  # noqa: E402
from clawops.agent.livekit._toolset import ClawOpsPhoneTools  # noqa: E402


def _make_call() -> CallSession:
    call = CallSession(
        call_id="c", from_number="010", to_number="070", account_id="AC"
    )
    call._transfer_fn = AsyncMock(return_value={"status": "ok"})
    call._hangup_fn = AsyncMock()
    return call


class _Caps:
    def __init__(self, audio_output: bool) -> None:
        self.audio_output = audio_output


class _RealtimeLLM:
    def __init__(self, audio_output: bool) -> None:
        self.capabilities = _Caps(audio_output)


class _FakeSession:
    def __init__(self, llm=None, tts=None) -> None:
        self.llm = llm
        self.tts = tts


class _FakeAgent:
    # Agent 쪽 컴포넌트는 미설정 시 NOT_GIVEN 이지만, 여기선 세션이 이긴다.
    llm = None
    tts = None


# ── _validate 가드 ──────────────────────────────────────────────


def test_text_modality_without_tts_raises() -> None:
    """modalities=['text'] + tts 없음 = 말 못 하는 에이전트. 우리가 막는다."""
    session = _FakeSession(llm=_RealtimeLLM(audio_output=False), tts=None)
    with pytest.raises(ValueError, match="소리를 내지 못합니다"):
        _validate(session, _FakeAgent())


def test_text_modality_with_tts_ok() -> None:
    session = _FakeSession(llm=_RealtimeLLM(audio_output=False), tts=object())
    _validate(session, _FakeAgent())  # 예외 없음


def test_audio_modality_with_tts_warns(caplog: pytest.LogCaptureFixture) -> None:
    """modalities=['audio'] + tts = TTS 가 조용히 무시됨. 경고를 띄운다."""
    session = _FakeSession(llm=_RealtimeLLM(audio_output=True), tts=object())
    with caplog.at_level("WARNING"):
        _validate(session, _FakeAgent())
    assert any("무시" in r.message for r in caplog.records)


def test_non_realtime_llm_skips_guard() -> None:
    """일반 LLM(파이프라인)은 audio_output capability 가 없다 — 통과."""
    session = _FakeSession(llm=object(), tts=None)
    _validate(session, _FakeAgent())  # 예외 없음


# ── transfer_call 인자 검증 (기존 버그 회귀 방지) ──────────────


async def test_transfer_rejects_bad_destination_type_before_firing() -> None:
    """잘못된 인자를 fire-and-forget 전에 잡아야 한다.

    _builtin_tool_schemas.py:194 는 ensure_future 를 try/except 로 감쌌지만,
    ensure_future 는 즉시 반환하므로 인자 오류가 future 안에서 조용히 터진다.
    여기서는 transfer_fn 이 아예 호출되지 않아야 한다.
    """
    tools = ClawOpsPhoneTools()
    call = _make_call()
    tools.set_call(call)

    result = await tools._transfer_call(None, to="010", destination_type="carrier")

    assert "Error" in result
    call._transfer_fn.assert_not_awaited()


async def test_transfer_rejects_empty_to() -> None:
    tools = ClawOpsPhoneTools()
    call = _make_call()
    tools.set_call(call)

    result = await tools._transfer_call(None, to="   ")

    assert "Error" in result
    call._transfer_fn.assert_not_awaited()


async def test_transfer_valid_args_fires() -> None:
    tools = ClawOpsPhoneTools()
    call = _make_call()
    tools.set_call(call)

    result = await tools._transfer_call(None, to="01012345678")

    assert "transfer_initiated" in result
    await asyncio.sleep(0)  # fire-and-forget 태스크가 돌 틈
    call._transfer_fn.assert_awaited_once()


async def test_transfer_forwards_caller_id_mode() -> None:
    """LiveKit 툴셋은 transfer_call 을 따로 정의한다 — 공용 스키마와 따로 놀면 안 된다.

    0.45.0 에서 CallSession.transfer 와 내장 도구 스키마에는 caller_id_mode 를 넣었는데
    이 파일만 빠져 있었다. LiveKit 을 쓰는 사용자만 조용히 이 기능이 없는 상태가 된다.
    """
    tools = ClawOpsPhoneTools()
    call = _make_call()
    tools.set_call(call)

    result = await tools._transfer_call(None, to="01012345678", caller_id_mode="original")

    assert "transfer_initiated" in result
    await asyncio.sleep(0)
    # transfer() 는 서버로 나갈 payload 를 위치인자 dict 로 넘긴다 — 키는 camelCase 다.
    payload = call._transfer_fn.await_args.args[0]
    assert payload["callerIdMode"] == "original"


async def test_transfer_rejects_bad_caller_id_mode_before_firing() -> None:
    """서버는 모르는 값을 조용히 무시한다 — 오타면 켠 줄 알고 계정 번호가 나간다."""
    tools = ClawOpsPhoneTools()
    call = _make_call()
    tools.set_call(call)

    result = await tools._transfer_call(None, to="010", caller_id_mode="origianl")

    assert "Error" in result and "caller_id_mode" in result
    call._transfer_fn.assert_not_awaited()


async def test_tool_before_call_bound_errors_cleanly() -> None:
    tools = ClawOpsPhoneTools()
    with pytest.raises(RuntimeError, match="연결되지 않았"):
        await tools._hang_up(None)


# ── after_transfer="return" 은 결과를 돌려준다 ─────────────────


class _Speech:
    allow_interruptions = True


class _Ctx:
    def __init__(self) -> None:
        self.speech_handle = _Speech()

    def disallow_interruptions(self) -> None:
        self.speech_handle.allow_interruptions = False


async def test_transfer_return_waits_and_tells_the_model() -> None:
    tools = ClawOpsPhoneTools()
    call = _make_call()
    result: asyncio.Future = asyncio.get_running_loop().create_future()

    async def transfer(_payload: dict) -> dict:
        return await result

    call._transfer_fn = transfer
    tools.set_call(call)
    ctx = _Ctx()

    task = asyncio.create_task(tools._transfer_call(ctx, to="01012345678", after_transfer="return"))
    await asyncio.sleep(0.01)
    assert not task.done()
    assert ctx.speech_handle.allow_interruptions is False, "기다리는 동안 끼어들기를 막는다"

    result.set_result({"status": "no-answer"})
    body = json.loads(await task)
    assert body["status"] == "no-answer"
    assert "did not answer" in body["message"]
    assert ctx.speech_handle.allow_interruptions is True, "이어받는 발화는 끊을 수 있어야 한다"


async def test_transfer_return_failure_is_reported_as_error() -> None:
    tools = ClawOpsPhoneTools()
    call = _make_call()
    call._transfer_fn = AsyncMock(side_effect=RuntimeError("boom"))
    tools.set_call(call)
    result = await tools._transfer_call(_Ctx(), to="01012345678", after_transfer="return")
    assert result == "Error: boom"


async def test_transfer_return_after_interruption_does_not_start() -> None:
    class _Interrupted(_Ctx):
        def disallow_interruptions(self) -> None:
            raise RuntimeError("already interrupted")

    tools = ClawOpsPhoneTools()
    call = _make_call()
    tools.set_call(call)
    result = await tools._transfer_call(_Interrupted(), to="01012345678", after_transfer="return")
    assert result.startswith("Error")
    call._transfer_fn.assert_not_awaited()


async def test_transfer_return_ending_call_skips_the_reply() -> None:
    from livekit.agents.llm import StopResponse

    tools = ClawOpsPhoneTools()
    call = _make_call()
    call._transfer_fn = AsyncMock(return_value={"status": "canceled"})
    tools.set_call(call)
    with pytest.raises(StopResponse):
        await tools._transfer_call(_Ctx(), to="01012345678", after_transfer="return")


async def test_transfer_return_connection_loss_restores_interruptions() -> None:
    tools = ClawOpsPhoneTools()
    call = _make_call()
    never: asyncio.Future = asyncio.get_running_loop().create_future()

    async def transfer(_payload: dict) -> dict:
        return await never

    lost: asyncio.Future = asyncio.get_running_loop().create_future()
    call._transfer_fn = transfer
    call._transfer_connection_lost_fn = lambda: lost
    tools.set_call(call)
    ctx = _Ctx()
    task = asyncio.create_task(tools._transfer_call(ctx, to="010", after_transfer="return"))
    await asyncio.sleep(0.01)
    lost.set_result(None)
    assert json.loads(await task)["status"] == "unknown"
    assert ctx.speech_handle.allow_interruptions is True
    never.cancel()


# ── hang_up 은 작별 인사 재생을 마친 뒤 끊는다 ─────────────────


class _PlayoutCtx:
    """RunContext 중 hang_up 이 쓰는 두 가지만."""

    def __init__(self, *, interrupted: bool = False) -> None:
        self.played = asyncio.Event()
        self.interrupted = interrupted
        self.allow_interruptions = True

    def disallow_interruptions(self) -> None:
        if self.interrupted:
            raise RuntimeError("already interrupted")
        self.allow_interruptions = False

    async def wait_for_playout(self) -> None:
        await self.played.wait()


async def test_hang_up_waits_for_farewell_playout() -> None:
    from livekit.agents.llm import StopResponse

    tools = ClawOpsPhoneTools()
    call = _make_call()
    tools.set_call(call)
    ctx = _PlayoutCtx()

    async def finish_playout() -> None:
        await asyncio.sleep(0.1)
        call._hangup_fn.assert_not_awaited()  # 인사가 재생되는 동안엔 끊지 않는다
        assert ctx.allow_interruptions is False  # 맞장구가 인사를 끊어 도구가 취소되지 않게
        ctx.played.set()

    results = await asyncio.gather(tools._hang_up(ctx), finish_playout(), return_exceptions=True)
    assert isinstance(results[0], StopResponse)  # 끊은 뒤 응답을 만들지 않는다
    call._hangup_fn.assert_awaited_once()


async def test_hang_up_playout_wait_has_a_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from livekit.agents.llm import StopResponse

    from clawops.agent.livekit import _toolset

    monkeypatch.setattr(_toolset, "HANG_UP_PLAYOUT_CAP_S", 0.05)
    tools = ClawOpsPhoneTools()
    call = _make_call()
    tools.set_call(call)

    with pytest.raises(StopResponse):
        await asyncio.wait_for(tools._hang_up(_PlayoutCtx()), 2.0)  # 재생 완료 신호가 끝내 안 와도
    call._hangup_fn.assert_awaited_once()


async def test_hang_up_skipped_when_caller_already_interrupted() -> None:
    tools = ClawOpsPhoneTools()
    call = _make_call()
    tools.set_call(call)

    out = await tools._hang_up(_PlayoutCtx(interrupted=True))
    assert "끊지 않았습니다" in out
    call._hangup_fn.assert_not_awaited()
