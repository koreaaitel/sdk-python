"""Provider Protocol 정의. 유저가 이 Protocol에 맞추면 아무 프로바이더나 사용 가능."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from .._session import CallSession


# ── Session Protocol ─────────────────────────────────────────

@runtime_checkable
class Session(Protocol):
    """Realtime 또는 Pipeline 세션의 공통 인터페이스.

    ClawOpsAgent는 이 Protocol을 구현하는 객체를 받아 통화를 처리한다.
    """

    async def start(self, call: CallSession) -> None:
        """세션 시작 (WS 연결, 인사말 등). prewarm + attach 의 thin wrapper."""
        ...

    async def prewarm(self) -> None:
        """LLM WS 연결 + session.update 만 수행한다. CallSession 없이 호출 가능.

        prewarm 후 도착하는 audio delta 는 내부 버퍼에 누적된다.
        attach() 호출 시 실제 CallSession 으로 flush.
        """
        ...

    async def attach(self, call: CallSession) -> None:
        """prewarmed 세션에 실제 CallSession 을 부착하고 버퍼를 flush 한다."""
        ...

    async def feed_audio(self, audio: bytes, timestamp: int) -> None:
        """인바운드 오디오(G.711 ulaw) 전달."""
        ...

    async def feed_dtmf(self, digits: str) -> None:
        """DTMF digit을 LLM 컨텍스트에 주입하고 응답을 트리거한다."""
        ...

    async def stop(self) -> None:
        """세션 종료 및 리소스 정리."""
        ...

    def get_telemetry(self) -> dict[str, Any] | None:
        """Return session telemetry data."""
        ...


# slots=True 는 3.10+ 라 쓰지 않는다 — requires-python 은 >=3.9 다(clawops#1252).
@dataclass(frozen=True)
class SpeechEvent:
    """STT 이벤트.

    - interim: 사용자가 말하고 있음 (barge-in 트리거용, transcript는 불완전할 수 있음)
    - final: 발화 완료 (확정된 transcript)
    """
    type: Literal["interim", "final"]
    transcript: str


@runtime_checkable
class STT(Protocol):
    async def transcribe(self, audio_stream: AsyncIterator[bytes]) -> AsyncIterator[SpeechEvent]:
        """오디오 스트림(PCM16) -> SpeechEvent 스트림."""
        ...

@runtime_checkable
class LLM(Protocol):
    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """메시지 -> 텍스트 응답 스트림."""
        ...

@runtime_checkable
class TTS(Protocol):
    def synthesize(self, text_stream: AsyncIterator[str]) -> AsyncIterator[bytes]:
        """텍스트 스트림 -> 오디오(PCM16) 스트림."""
        ...
