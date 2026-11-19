"""Typecast HTTP streaming TTS for the native agent pipeline."""
from __future__ import annotations

import os
import re
import struct
from collections.abc import AsyncIterator, Iterator
from typing import Any

import aiohttp

TYPECAST_STREAM_URL = "https://api.typecast.ai/v1/text-to-speech/stream"
_SAMPLE_RATE = 32000
_STREAMING_SIZE = 0xFFFFFFFF
_MAX_HEADER_SIZE = 64 * 1024
_MAX_TEXT_LENGTH = 2000
_LANGUAGES = {"ko": "kor", "en": "eng", "ja": "jpn", "zh": "zho"}
# The pipeline resamples each chunk independently. One 8 kHz sample spans
# four 16-bit samples at 32 kHz, so keep those groups intact across chunks.
_PCM_ALIGNMENT = 8


def _text_parts(text: str) -> Iterator[str]:
    text = text.strip()
    while len(text) > _MAX_TEXT_LENGTH:
        space = text.rfind(" ", 0, _MAX_TEXT_LENGTH + 1)
        end = space if space > 0 else _MAX_TEXT_LENGTH
        yield text[:end].rstrip()
        text = text[end:].lstrip()
    if text:
        yield text


def _wav_data_header(header: bytearray) -> tuple[int, int | None] | None:
    """Return the PCM offset and data length once the WAV header is complete."""
    if len(header) < 12:
        return None
    if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        raise ValueError("Typecast audio response is not a WAV stream")

    offset = 12
    has_format = False
    while offset + 8 <= len(header):
        kind, size = struct.unpack_from("<4sI", header, offset)
        data_start = offset + 8
        if kind == b"data":
            if not has_format:
                raise ValueError("Typecast WAV stream is missing its format chunk")
            return data_start, None if size == _STREAMING_SIZE else size

        next_offset = data_start + size + size % 2
        if next_offset + 8 > _MAX_HEADER_SIZE:
            raise ValueError("Typecast WAV header is too large")
        if next_offset > len(header):
            return None
        if kind == b"fmt ":
            if size < 16:
                raise ValueError("Typecast WAV format chunk is truncated")
            encoding, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", header, data_start)
            if (encoding, channels, rate, bits) != (1, 1, _SAMPLE_RATE, 16):
                raise ValueError("Typecast WAV audio must use mono 32 kHz PCM16")
            has_format = True
        offset = next_offset
    return None


class TypecastTTS:
    def __init__(
        self,
        *,
        voice: str,
        model: str = "ssfm-v31",
        language: str | None = "ko",
        speed: float | None = None,
        api_key: str | None = None,
    ) -> None:
        voice = voice.strip()
        if not voice:
            raise ValueError("Typecast voice is required")
        key = api_key if api_key is not None else os.getenv("TYPECAST_API_KEY")
        if not key:
            raise ValueError("Set TYPECAST_API_KEY or pass api_key to TypecastTTS")
        if speed is not None and not 0.5 <= speed <= 2.0:
            raise ValueError("Typecast speed must be between 0.5 and 2.0")

        code = language.strip().lower().split("-", 1)[0] if language else ""
        self._language = _LANGUAGES.get(code, code if re.fullmatch(r"[a-z]{3}", code) else None)
        self._api_key = key
        self._voice = voice
        self._model = model
        self._speed = speed

    @property
    def provider(self) -> str:
        return "typecast"

    @property
    def model(self) -> str:
        return self._model

    @property
    def voice_id(self) -> str:
        return self._voice

    @property
    def sample_rate(self) -> int:
        return _SAMPLE_RATE

    async def synthesize(self, text_stream: AsyncIterator[str]) -> AsyncIterator[bytes]:
        """Yield raw 32 kHz, mono PCM16 while Typecast synthesizes each text segment."""
        output: dict[str, Any] = {"audio_format": "wav"}
        if self._speed is not None:
            output["audio_tempo"] = self._speed
        request: dict[str, Any] = {"voice_id": self._voice, "model": self._model, "output": output}
        if self._language is not None:
            request["language"] = self._language

        pending = bytearray()
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async for text in text_stream:
                for part in _text_parts(text):
                    async with session.post(
                        TYPECAST_STREAM_URL,
                        json={**request, "text": part},
                        headers={"X-API-KEY": self._api_key},
                        allow_redirects=False,
                    ) as response:
                        response.raise_for_status()
                        if response.status != 200:
                            raise ValueError(f"Unexpected Typecast HTTP status {response.status}")
                        header: bytearray | None = bytearray()
                        remaining: int | None = None
                        received = 0
                        async for chunk in response.content.iter_chunked(4096):
                            if header is not None:
                                header.extend(chunk)
                                data = _wav_data_header(header)
                                if data is None:
                                    continue
                                start, remaining = data
                                chunk = bytes(header[start:])
                                header = None
                            if remaining is not None:
                                chunk = chunk[:remaining]
                                remaining -= len(chunk)
                            received += len(chunk)
                            pending.extend(chunk)
                            ready = len(pending) - len(pending) % _PCM_ALIGNMENT
                            if ready:
                                pcm = bytes(pending[:ready])
                                del pending[:ready]
                                yield pcm
                            if remaining == 0:
                                break
                        if not received or received % 2 or remaining:
                            raise ValueError("Typecast WAV audio is empty or truncated")
        if pending:
            yield bytes(pending)
