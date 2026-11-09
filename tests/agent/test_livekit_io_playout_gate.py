"""재생 게이트(set_playout_gate) — 엔진이 오디오를 보관하는 동안 재생 완료 대기 상한을 세지 않는다.

CASE-2026-004501: 관리형 발신은 받기 전에 인사를 만들어 두고, 엔진이 받은 뒤 오디오와 mark 를
함께 푼다. 그 사이 「오디오 길이 + 여유」 상한이 먼저 끝나면 인사를 「다 말했다」고 판단해
「첫 인사는 끝까지 말하기」가 인사가 들리기도 전에 풀린다.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("livekit.agents", reason="clawops[livekit] 미설치")

from livekit import rtc
from livekit.agents.voice import io

from clawops.agent._session import CallSession
from clawops.agent.livekit import _io
from clawops.agent.livekit._io import SAMPLE_RATE, ClawOpsAudioOutput


class _HoldingMediaWs:
    """mark 를 `echo` 가 set 될 때까지 돌려주지 않는 엔진(보관 중) 흉내."""

    def __init__(self, echo: asyncio.Event) -> None:
        self.is_connected = True
        self._echo = echo

    async def flush(self) -> None:
        return None

    async def send_mark(self, name: str) -> None:
        return None

    async def wait_for_mark(self, name: str, timeout: float = 5.0) -> None:
        try:
            await asyncio.wait_for(self._echo.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass


def _make_call(ws: _HoldingMediaWs) -> CallSession:
    call = CallSession(call_id="c", from_number="1", to_number="2", account_id="AC")
    call._send_audio_fn = AsyncMock()
    call._send_clear_fn = AsyncMock()
    call._media_ws = ws
    return call


def _frame(samples: int) -> rtc.AudioFrame:
    return rtc.AudioFrame(
        data=b"\x00\x00" * samples, sample_rate=SAMPLE_RATE, num_channels=1, samples_per_channel=samples
    )


async def _play_one_segment(out: ClawOpsAudioOutput) -> list[tuple[float, io.PlaybackFinishedEvent]]:
    events: list[tuple[float, io.PlaybackFinishedEvent]] = []
    out.on("playback_finished", lambda ev: events.append((time.monotonic(), ev)))
    await out.capture_frame(_frame(800))  # 100ms
    out.flush()
    return events


@pytest.fixture(autouse=True)
def _short_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    # 상한 = 0.1(오디오) + 0.05 = 0.15초. 게이트는 그보다 훨씬 늦게 연다.
    monkeypatch.setattr(_io, "_MARK_TIMEOUT_MARGIN", 0.05)


async def test_gate_closed_does_not_count_budget_until_opened() -> None:
    echo, gate = asyncio.Event(), asyncio.Event()
    out = ClawOpsAudioOutput(_make_call(_HoldingMediaWs(echo)))
    out.set_playout_gate(gate)
    start = time.monotonic()
    events = await _play_one_segment(out)

    await asyncio.sleep(0.4)  # 상한(0.15초)을 훌쩍 넘겨도 게이트가 닫혀 있으면 끝내지 않는다
    assert events == []

    gate.set()  # 받음 — 엔진이 오디오를 풀었다
    await asyncio.sleep(0.05)
    echo.set()  # 그 오디오가 다 재생됐다
    await asyncio.sleep(0.05)

    assert len(events) == 1
    finished_at, ev = events[0]
    assert ev.interrupted is False
    assert finished_at - start >= 0.45


async def test_budget_counts_from_gate_open() -> None:
    echo, gate = asyncio.Event(), asyncio.Event()
    out = ClawOpsAudioOutput(_make_call(_HoldingMediaWs(echo)))
    out.set_playout_gate(gate)
    events = await _play_one_segment(out)

    await asyncio.sleep(0.3)
    gate.set()
    opened = time.monotonic()
    await asyncio.sleep(0.4)  # echo 가 끝내 안 오면 게이트가 열린 뒤 상한만큼 지나 끝낸다

    assert len(events) == 1
    assert 0.1 <= events[0][0] - opened <= 0.3


async def test_mark_before_gate_finishes_immediately() -> None:
    """mark 를 보관하지 않는 엔진(즉시 echo)에서는 게이트를 기다리지 않는다 — 종전 동작."""
    echo, gate = asyncio.Event(), asyncio.Event()
    echo.set()
    out = ClawOpsAudioOutput(_make_call(_HoldingMediaWs(echo)))
    out.set_playout_gate(gate)
    events = await _play_one_segment(out)
    await asyncio.sleep(0.05)

    assert len(events) == 1
    assert events[0][1].interrupted is False


async def test_without_gate_budget_applies_as_before() -> None:
    echo = asyncio.Event()
    out = ClawOpsAudioOutput(_make_call(_HoldingMediaWs(echo)))
    start = time.monotonic()
    events = await _play_one_segment(out)
    await asyncio.sleep(0.3)

    assert len(events) == 1
    assert events[0][0] - start < 0.3


async def test_barge_in_while_gate_closed_is_interrupted() -> None:
    echo, gate = asyncio.Event(), asyncio.Event()
    out = ClawOpsAudioOutput(_make_call(_HoldingMediaWs(echo)))
    out.set_playout_gate(gate)
    events = await _play_one_segment(out)
    await asyncio.sleep(0.05)

    out.clear_buffer()
    await asyncio.sleep(0.05)

    assert len(events) == 1
    assert events[0][1].interrupted is True
