"""ClawOps Agent - AI 음성 에이전트 프레임워크."""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from .._exceptions import AgentAuthRejectedError, AgentConnectionError, AgentError
from ._agent import ClawOpsAgent, ToolConfig
from ._deploy_checks import clear_stale_ready_marker
from ._incoming import IncomingCall
from ._builtin_tools import BuiltinTool
from ._media_ws import MediaWebSocket
from ._session import CallSession, DtmfCollectorBusy
from ._tool import ToolRegistry, function_tool
from .pipeline import Session

if TYPE_CHECKING:
    from .pipeline import OpenAIRealtime, GeminiRealtime

# **낡은 준비 표시를 import 시점에 지운다.**
#
# 이 모듈을 import 했다는 것은 새 에이전트 프로세스가 시작됐다는 뜻이고, 그러면 지금 남아
# 있는 준비 표시는 정의상 이전 프로세스의 것이다.
#
# ClawOpsAgent 생성자나 connect() 로 미루면 늦다 — 무거운 import 와 모델 클라이언트 초기화가
# **그보다 먼저** 일어나기 때문이다. read-only rootfs 배포가 흔히 마운트하는 emptyDir `/tmp`
# 는 pod 수명 동안 살아 있어서, 컨테이너가 SIGKILL 로 죽고 재시작되면 이전 표시가 그대로
# 있고 그 사이 pod 가 Ready 로 판정된다.
#
# 2026-09-10 kind 실측: 생성자에서 지웠더니 pod 가 연결(t+38.7s)보다 **12초 먼저**
# Ready(t+26.6s) 가 됐다. 그 12초에 오는 전화는 전부 죽는다.
clear_stale_ready_marker()

__all__ = [
    "AgentAuthRejectedError",
    "AgentConnectionError",
    "AgentError",
    "BuiltinTool",
    "CallSession",
    "DtmfCollectorBusy",
    "IncomingCall",
    "ClawOpsAgent",
    "MediaWebSocket",
    "ToolConfig",
    "ToolRegistry",
    "function_tool",
    "Session",
    "OpenAIRealtime",
    "GeminiRealtime",
]

_LAZY = {
    "OpenAIRealtime": ".pipeline",
    "GeminiRealtime": ".pipeline",
}


def __getattr__(name: str):
    if name in _LAZY:
        mod = importlib.import_module(_LAZY[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
