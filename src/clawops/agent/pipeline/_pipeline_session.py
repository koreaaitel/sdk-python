"""PipelineSession: STT -> LLM -> TTS 파이프라인 오케스트레이터."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator

from .._audio import ulaw_to_pcm16, pcm16_to_ulaw, resample_pcm16
from .._builtin_tools import BuiltinTool
from .._hold_audio import HoldAudioPlayer, hold_audio_allowed
from .._recorder import AudioRecorder
from .._session import CallSession
from .._tool import ToolRegistry
from ._base import STT, LLM, TTS, SpeechEvent
from ._builtin_tool_schemas import get_builtin_tool_schemas, execute_builtin_tool, BUILTIN_TOOL_NAMES

log = logging.getLogger("clawops.agent.pipeline")

_SENTENCE_END = re.compile(r"[.!?。！？]\s*$")


def _to_chat_tools(realtime_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Realtime API 포맷 tool 스키마를 Chat Completions 포맷으로 변환.

    Realtime: {"type": "function", "name": ..., "description": ..., "parameters": ...}
    Chat:     {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    """
    result = []
    for tool in realtime_tools:
        if "function" in tool and isinstance(tool["function"], dict):
            result.append(tool)
        else:
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                    },
                }
            )
    return result


class PipelineSession:
    def __init__(
        self,
        *,
        stt: STT,
        llm: LLM,
        tts: TTS,
        system_prompt: str = "",
        greeting: bool = True,
        language: str = "ko",
        tool_registry: ToolRegistry | None = None,
        recorder: AudioRecorder | None = None,
    ) -> None:
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._system_prompt = system_prompt
        self._tools = tool_registry or ToolRegistry()
        self._builtin_tools: set[BuiltinTool] | None = None
        self._greeting = greeting
        self._language = language
        self._recorder = recorder

        from ._buffering_call import _BufferingCall as _BC

        self._call: CallSession | _BC | None = None
        self._messages: list[dict[str, Any]] = []
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._running = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._current_response_task: asyncio.Task[Any] | None = None
        self._first_audio_logged: bool = False
        self._sent_audio_chunks = 0
        self._pending_respond_task: asyncio.Task[Any] | None = None
        self._hold_audio_chunks: list[bytes] | None = None

    def set_hold_audio(self, chunks: list[bytes]) -> None:
        """Tool 실행 중 재생할 hold audio 청크를 ���정한다."""
        self._hold_audio_chunks = chunks

    def set_tool_registry(self, registry: ToolRegistry) -> None:
        """콜별로 fork된 ToolRegistry를 주입한다."""
        self._tools = registry

    def set_builtin_tools(self, tools: set[BuiltinTool]) -> None:
        """사용할 내장 도구 set을 지정."""
        self._builtin_tools = tools

    def set_recorder(self, recorder: AudioRecorder) -> None:
        """콜별로 생성된 AudioRecorder를 주입한다."""
        self._recorder = recorder

    def get_telemetry(self) -> dict[str, Any]:
        llm = self._llm
        stt = self._stt
        tts = self._tts
        return {
            "sessionType": "pipeline",
            "llm": {"provider": getattr(llm, "provider", None), "model": getattr(llm, "model", None)}
            if hasattr(llm, "provider")
            else None,
            "stt": {"provider": getattr(stt, "provider", None), "model": getattr(stt, "model", None)}
            if hasattr(stt, "provider")
            else None,
            "tts": {"provider": getattr(tts, "provider", None), "model": getattr(tts, "model", None)}
            if hasattr(tts, "provider")
            else None,
            "voice": getattr(tts, "voice_id", None),
            "language": self._language,
            "greetingEnabled": self._greeting,
            "recordingEnabled": self._recorder is not None,
            "toolCount": 0,
            "mcpServerCount": 0,
            "builtinTools": [],
        }

    async def prewarm(self) -> None:
        """PipelineSession prewarm — CallSession 없이 호출 가능.

        STT/LLM/TTS 자원은 lazy 이므로 BufferingCall 만 세팅하고
        greeting trigger 가 있으면 TTS 합성을 미리 시작한다.
        """
        from ._buffering_call import _BufferingCall

        self._call = _BufferingCall()
        self._first_audio_logged = False
        self._running = True
        self._messages = [{"role": "system", "content": self._system_prompt}]
        self._tasks.append(asyncio.create_task(self._stt_loop()))
        if self._greeting:
            self._tasks.append(asyncio.create_task(self._generate_greeting()))
        log.info("PipelineSession prewarmed")

    async def attach(self, call: CallSession) -> None:
        """prewarmed 세션에 실제 CallSession 부착 + 버퍼 flush."""
        from ._buffering_call import drain_into

        prev = self._call
        self._call = call
        flushed = await drain_into(prev, call)
        if flushed:
            self._first_audio_logged = True

    async def start(self, call: CallSession) -> None:
        """기존 호환 경로 — prewarm + attach wrapper."""
        await self.prewarm()
        await self.attach(call)

    async def feed_audio(self, ulaw: bytes, timestamp: int) -> None:
        if not self._running:
            return
        await self._audio_queue.put(ulaw)

    async def feed_dtmf(self, digits: str) -> None:
        """DTMF digit을 LLM 컨텍스트에 주입하고 응답을 트리거한다."""
        self._messages.append(
            {
                "role": "user",
                "content": f"[DTMF 입력: {digits}]",
            }
        )
        await self._respond()

    async def stop(self) -> None:
        self._running = False
        await self._audio_queue.put(None)
        if self._pending_respond_task and not self._pending_respond_task.done():
            self._pending_respond_task.cancel()
        if self._current_response_task and not self._current_response_task.done():
            self._current_response_task.cancel()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()
        log.info("PipelineSession stopped")

    async def _audio_stream(self) -> AsyncIterator[bytes]:
        while self._running:
            chunk = await self._audio_queue.get()
            if chunk is None:
                break
            pcm8k = ulaw_to_pcm16(chunk)
            pcm16k = resample_pcm16(pcm8k, from_rate=8000, to_rate=16000)
            yield pcm16k

    # ── STT 이벤트 처리 ──────────────────────────────────────

    async def _stt_loop(self) -> None:
        try:
            async for event in self._stt.transcribe(self._audio_stream()):
                if event.type == "interim":
                    await self._handle_interim_speech(event)
                elif event.type == "final":
                    await self._handle_final_transcript(event)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"STT loop error: {e}")

    async def _handle_interim_speech(self, event: SpeechEvent) -> None:
        """사용자 발화 감지 (interim) → AI 오디오가 재생 중이면 즉시 중단."""
        if self._sent_audio_chunks > 0:
            log.info(f'Barge-in: "{event.transcript[:30]}" — clearing AI audio')
            # 응답 생성 중이면 취소
            if self._current_response_task and not self._current_response_task.done():
                self._current_response_task.cancel()
                try:
                    await self._current_response_task
                except asyncio.CancelledError:
                    pass
            # Twilio 버퍼에 남아있는 오디오 클리어
            if self._call:
                await self._call.clear_audio()
            self._sent_audio_chunks = 0

    async def _handle_final_transcript(self, event: SpeechEvent) -> None:
        """발화 완료 (final) → 메시지 추가 후 응답 생성."""
        transcript = event.transcript
        if not transcript.strip():
            return
        log.info(f"STT: {transcript}")
        if self._call:
            await self._call._emit("transcript", "user", transcript)

        self._messages.append({"role": "user", "content": transcript})

        if self._sent_audio_chunks > 0:
            # AI가 아직 말하고 있었다면 인터럽트 (interim에서 이미 중단했을 수도 있음)
            await self._interrupt()
            self._current_response_task = asyncio.create_task(self._respond())
        else:
            # AI가 아직 말하기 전 → 현재 응답 취소하고 debounce
            if self._current_response_task and not self._current_response_task.done():
                self._current_response_task.cancel()
                try:
                    await self._current_response_task
                except asyncio.CancelledError:
                    pass
                log.info("Response cancelled (no audio sent yet)")
            if self._pending_respond_task and not self._pending_respond_task.done():
                self._pending_respond_task.cancel()
            self._pending_respond_task = asyncio.create_task(self._debounced_respond(0.5))

    # ── 응답 생성 ────────────────────────────────────────────

    async def _debounced_respond(self, delay: float) -> None:
        """delay 동안 추가 입력을 기다린 후 응답 시작."""
        await asyncio.sleep(delay)
        log.info("Debounce expired, starting response")
        self._current_response_task = asyncio.create_task(self._respond())

    async def _generate_greeting(self) -> None:
        await asyncio.sleep(0.5)
        self._messages.append({"role": "user", "content": "[통화 시작]"})
        self._current_response_task = asyncio.create_task(self._respond())

    async def _respond(self) -> None:
        if not self._call:
            return
        try:
            tool_schemas = _to_chat_tools(self._tools.to_openai_tools())
            tool_schemas.extend(get_builtin_tool_schemas(self._builtin_tools, fmt="chat"))

            pending_tool_calls: dict[str, Any] | None = None

            async def text_stream() -> AsyncIterator[str]:
                nonlocal pending_tool_calls
                buffer = ""
                async for token in self._llm.generate(self._messages, tools=tool_schemas):
                    if token.startswith('{"type":"tool_calls"') or token.startswith('{"type": "tool_calls"'):
                        if buffer.strip():
                            yield buffer
                            buffer = ""
                        pending_tool_calls = json.loads(token)
                        return
                    buffer += token
                    if _SENTENCE_END.search(buffer):
                        log.info(f"LLM sentence: {buffer[:80]}")
                        yield buffer
                        buffer = ""
                if buffer.strip():
                    log.info(f"LLM final: {buffer[:80]}")
                    yield buffer

            text_chunks: list[str] = []

            async def tee_text() -> AsyncIterator[str]:
                async for chunk in text_stream():
                    text_chunks.append(chunk)
                    yield chunk

            tts_sample_rate = getattr(self._tts, "sample_rate", 24000)
            self._sent_audio_chunks = 0

            audio_stream = self._tts.synthesize(tee_text())
            try:
                async for audio in audio_stream:
                    if not self._running or not self._call:
                        log.warning("TTS audio received but session stopped")
                        break
                    pcm8k = resample_pcm16(audio, from_rate=tts_sample_rate, to_rate=8000)
                    if not pcm8k:
                        continue
                    ulaw = pcm16_to_ulaw(pcm8k)
                    # delta 를 **자르지도 채우지도 않고** 그대로 넘긴다.
                    #
                    # 예전엔 여기서 160바이트로 자르고 자투리를 무음으로 채웠다. 이월이
                    # 없어서 **delta 마다** 0~19ms 무음이 박혔다 — livekit 경로(세그먼트당
                    # 1회)보다 훨씬 잦다. 프레이밍은 20ms 클럭을 쥔 엔진 몫이다.
                    if not self._first_audio_logged:
                        from ._buffering_call import log_first_realtime_audio

                        log_first_realtime_audio(self._call)
                        self._first_audio_logged = True
                    await self._call.send_audio(ulaw)
                    # barge-in 게이트용 `> 0` 플래그다 — 정확한 청크 수가 아니어도 된다.
                    self._sent_audio_chunks += 1
            finally:
                # Cancellation while sending audio leaves the iterator paused at its yield.
                close_audio = getattr(audio_stream, "aclose", None)
                if callable(close_audio):
                    await close_audio()

            assistant_text = "".join(text_chunks)
            if assistant_text.strip():
                log.info(f"Assistant: {assistant_text[:100]}")
                if self._call:
                    await self._call._emit("transcript", "assistant", assistant_text)

            if pending_tool_calls is not None:
                pre_text = assistant_text.strip() or None
                await self._handle_tool_calls(pending_tool_calls, pre_text=pre_text)
            else:
                if assistant_text.strip():
                    self._messages.append({"role": "assistant", "content": assistant_text})

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"Response error: {e}")

    async def _handle_tool_calls(self, data: dict[str, Any], *, pre_text: str | None = None) -> None:
        tool_calls = data.get("tool_calls", [])
        self._messages.append(
            {
                "role": "assistant",
                "content": pre_text,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )

        player = (
            HoldAudioPlayer(self._call, self._hold_audio_chunks)
            if self._hold_audio_chunks
            and self._call
            and hold_audio_allowed(tc["function"]["name"] for tc in tool_calls)
            else None
        )
        if player:
            await player.start()

        try:
            for tc in tool_calls:
                func_name = tc["function"]["name"]
                call_id = tc["id"]
                args = json.loads(tc["function"]["arguments"])

                # Builtin tool 처리
                if func_name in BUILTIN_TOOL_NAMES and self._call:
                    if self._call:
                        self._call.metrics.record_tool_call()
                    result = await execute_builtin_tool(func_name, args, self._call)
                    if result == "":  # hang_up
                        return
                    if result is not None:
                        self._messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
                        continue

                # Custom / MCP tool 처리
                if self._call:
                    self._call.metrics.record_tool_call()
                try:
                    result = await self._tools.call(func_name, args)
                except Exception as e:
                    log.error(f"Tool call failed: {func_name}: {e}")
                    if self._call:
                        self._call.metrics.record_tool_error(e)
                    result = f"Error: {e}"
                self._messages.append({"role": "tool", "tool_call_id": call_id, "content": str(result)})
        finally:
            if player:
                await player.stop()

        await self._respond()

    async def _interrupt(self) -> None:
        if self._current_response_task and not self._current_response_task.done():
            self._current_response_task.cancel()
            try:
                await self._current_response_task
            except asyncio.CancelledError:
                pass
            if self._call:
                await self._call.clear_audio()
            self._sent_audio_chunks = 0
            log.info("Response interrupted")
