from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from ._base import Session, STT, LLM, TTS, SpeechEvent
from .stt import DeepgramSTT
from .llm import (
    OpenAILLM,
    AnthropicLLM,
    GeminiLLM,
    OpenAICompatibleLLM,
    OllamaLLM,
    MistralLLM,
    GroqLLM,
    PerplexityLLM,
    TogetherLLM,
    FireworksLLM,
    DeepSeekLLM,
    XaiLLM,
)
from .tts import ElevenLabsTTS, TypecastTTS
from ._pipeline_session import PipelineSession

if TYPE_CHECKING:
    from .realtime import OpenAIRealtime, GeminiRealtime

__all__ = [
    "Session",
    "STT", "LLM", "TTS", "SpeechEvent",
    "DeepgramSTT",
    "OpenAILLM", "AnthropicLLM", "GeminiLLM",
    "OpenAICompatibleLLM", "OllamaLLM",
    "MistralLLM", "GroqLLM", "PerplexityLLM",
    "TogetherLLM", "FireworksLLM", "DeepSeekLLM", "XaiLLM",
    "ElevenLabsTTS", "TypecastTTS",
    "PipelineSession",
    "OpenAIRealtime",
    "GeminiRealtime",
]

_LAZY = {
    "OpenAIRealtime": ".realtime",
    "GeminiRealtime": ".realtime",
}


def __getattr__(name: str):
    if name in _LAZY:
        mod = importlib.import_module(_LAZY[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
