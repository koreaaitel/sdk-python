"""LiveKit 내장 ``transfer_call`` 도구가 모델에게 돌려주는 값.

``after_transfer="return"`` 은 전환이 끝나면 AI 가 통화로 돌아온다. 그때 모델이 전환 결과를 모르면
담당자가 받지 않았는데도 사정을 모른 채 대화를 잇는다. 그래서 이 모드의 도구는 결과까지 기다렸다가
사실을 돌려준다. ``terminate`` 는 서버가 미디어 세션을 닫으므로 기다릴 수 없다.

⚠️ 자체 파이프라인·realtime 세션(``pipeline/``)은 기다리지 않는다. 파이프라인은 끼어들기가 대기를
취소해 짝 없는 tool_calls 로 대화 기록이 깨지고, Gemini 는 도구가 수신 루프를 막아 go_away 를 놓친다.

Node SDK ``src/agent/transfer-tool.ts`` 와 문구를 맞춘다.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

TRANSFER_INITIATED = json.dumps({"status": "transfer_initiated"})

_RESUME = " The caller is on the line with you again. Tell them and continue the conversation."
_FAILED_MESSAGES = {
    "no-answer": "The transfer target did not answer." + _RESUME,
    "busy": "The transfer target was busy." + _RESUME,
}
_FAILED_DEFAULT = "The transfer could not be connected." + _RESUME
_COMPLETED = (
    "The transfer target finished the call and the caller is on the line with you again. "
    "Ask whether they need anything else."
)
_CALL_ENDED = "The caller hung up. The call is over."


def transfer_result_for_model(result: dict[str, Any]) -> str:
    """서버의 전환 결과(``call.transfer.completed``·``failed`` 의 ``transfer``)를 도구 결과로 만든다."""
    status = result.get("status") or "failed"
    if status == "completed":
        message = _COMPLETED
    elif status == "canceled":
        message = _CALL_ENDED
    else:
        message = _FAILED_MESSAGES.get(status, _FAILED_DEFAULT)
    return json.dumps({"status": status, "message": message})


def is_call_over(result: str) -> bool:
    """도구 결과가 통화 종료인가. 들을 사람이 없으니 모델 응답을 만들지 않는 데 쓴다."""
    return result == _CALL_OVER


_CALL_OVER = transfer_result_for_model({"status": "canceled"})
_RESULT_UNKNOWN = json.dumps(
    {
        "status": "unknown",
        "message": "The transfer result is unavailable. If the caller speaks, continue the conversation.",
    }
)


async def wait_for_transfer_result(
    task: "asyncio.Future[dict[str, Any]]",
    *,
    call_ended: Callable[[], bool],
    connection_lost: "asyncio.Future[None] | None" = None,
) -> str:
    """전환 결과를 기다려 도구 결과로 돌려준다.

    결과 이벤트는 재전송되지 않아 control 연결이 끊긴 틈에 오면 사라진다. 그대로 기다리면 끼어들기가
    막힌 채 통화 끝까지 멈추므로, 연결이 끊기면 대기를 접고 모른다고 알린다.
    ``asyncio.wait`` 는 취소돼도 전환 요청(``task``)을 건드리지 않는다.
    """
    waits: set[Any] = {task}
    if connection_lost is not None:
        waits.add(connection_lost)
    try:
        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if connection_lost is not None:
            connection_lost.cancel()
    if not task.done():
        return _RESULT_UNKNOWN
    if task.cancelled():
        # 전환 대기는 통화 종료 정리나 drain 으로 취소된다. drain 은 통화가 살아 있다.
        return _CALL_OVER if call_ended() else _RESULT_UNKNOWN
    return transfer_result_for_model(task.result())
