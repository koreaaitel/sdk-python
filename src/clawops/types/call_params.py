from __future__ import annotations

from typing import Annotated, Literal, Union

from typing_extensions import Required, TypeAlias, TypedDict

from .._utils import PropertyInfo

# ── Call API 파라미터 ────────────────────────────────────────────────────────


class CallContextParam(TypedDict, total=False):
    """``agent_id`` 에이전트의 **이번 통화에만** 적용되는 컨텍스트.

    에이전트 자체의 설정은 그대로 두고 이 통화만 다르게 행동시킬 때 사용합니다.
    같은 에이전트로 동시에 거는 다른 통화에는 영향이 없습니다.
    """

    instruction: Required[Annotated[str, PropertyInfo(alias="Instruction")]]
    """이번 통화에서 수행할 실행 요구사항 (최대 4000자)."""

    variables: Annotated[dict[str, Union[str, float, bool]], PropertyInfo(alias="Variables")]
    """요구사항에서 참조할 통화별 구조화 데이터 (최대 50개)."""


CallerIdMode: TypeAlias = Literal["shared-010"]
"""Calls API에서 공용 010 회선을 선택하는 발신 방식."""


class _CallCreateOptions(TypedDict, total=False):
    """일반 발신과 공용 010 발신이 공유하는 파라미터."""

    to: Required[Annotated[str, PropertyInfo(alias="To")]]
    """수신 대상. 전화번호(PSTN) 또는 sip: URI(내선)."""

    status_callback: Annotated[str | None, PropertyInfo(alias="StatusCallback")]
    """통화 상태 변경 시 POST 요청을 받을 콜백 URL."""

    status_callback_event: Annotated[str | None, PropertyInfo(alias="StatusCallbackEvent")]
    """수신할 상태 이벤트 목록 (공백 구분)."""

    timeout: Annotated[int | None, PropertyInfo(alias="Timeout")]
    """발신 타임아웃 (초). 기본값: 60."""

    machine_detection: Annotated[Literal["Enable", "Hangup"] | None, PropertyInfo(alias="MachineDetection")]
    """자동응답기/음성사서함 감지(AMD)."""

    extra_headers: dict[str, str] | None
    extra_query: dict[str, object] | None
    timeout_: float | None
    idempotency_key: str | None
    """선택. ``Idempotency-Key`` 헤더 — 같은 키 재제출은 처음 결과를 돌려받는다."""


class CallCreateParams(_CallCreateOptions, total=False):
    """계정에 등록된 전화번호로 일반 발신할 때 쓰는 파라미터."""

    from_: Required[Annotated[str, PropertyInfo(alias="From")]]
    caller_id_mode: Annotated[None, PropertyInfo(alias="CallerIdMode")]
    sdk_agent_id: Annotated[None, PropertyInfo(alias="SdkAgentId")]
    url: Annotated[str | None, PropertyInfo(alias="Url")]
    agent_id: Annotated[str | None, PropertyInfo(alias="AgentId")]
    call_context: Annotated[CallContextParam | None, PropertyInfo(alias="CallContext")]
    call_flow_id: Annotated[str | None, PropertyInfo(alias="CallFlowId")]
    variables: Annotated[dict[str, Union[str, float, bool]] | None, PropertyInfo(alias="Variables")]


class _Shared010CallOptions(_CallCreateOptions, total=False):
    caller_id_mode: Required[Annotated[CallerIdMode, PropertyInfo(alias="CallerIdMode")]]


class _Shared010SdkCall(_Shared010CallOptions, total=False):
    sdk_agent_id: Required[Annotated[str, PropertyInfo(alias="SdkAgentId")]]
    from_: None
    agent_id: None
    url: None
    call_flow_id: None
    call_context: None
    variables: None


class _Shared010NumberCall(_Shared010CallOptions, total=False):
    from_: Required[Annotated[str, PropertyInfo(alias="From")]]
    sdk_agent_id: None
    agent_id: None
    url: None
    call_flow_id: None
    call_context: None
    variables: None


class _Shared010TargetCall(_Shared010CallOptions, total=False):
    from_: Annotated[str | None, PropertyInfo(alias="From")]
    sdk_agent_id: None


class _Shared010ManagedCall(_Shared010TargetCall, total=False):
    agent_id: Required[Annotated[str, PropertyInfo(alias="AgentId")]]
    call_context: Annotated[CallContextParam | None, PropertyInfo(alias="CallContext")]
    url: None
    call_flow_id: None
    variables: None


class _Shared010VoiceMLCall(_Shared010TargetCall, total=False):
    url: Required[Annotated[str, PropertyInfo(alias="Url")]]
    agent_id: None
    call_flow_id: None
    call_context: None
    variables: None


class _Shared010FlowCall(_Shared010TargetCall, total=False):
    call_flow_id: Required[Annotated[str, PropertyInfo(alias="CallFlowId")]]
    variables: Annotated[dict[str, Union[str, float, bool]] | None, PropertyInfo(alias="Variables")]
    agent_id: None
    url: None
    call_context: None


Shared010CallCreateParams: TypeAlias = Union[
    _Shared010SdkCall, _Shared010NumberCall, _Shared010ManagedCall, _Shared010VoiceMLCall, _Shared010FlowCall
]
"""공용 010 발신에는 번호에 연결된 SDK, SDK ID 또는 통화 처리 대상 하나를 지정합니다."""


class CallListParams(TypedDict, total=False):
    """통화 목록 조회 요청 파라미터."""

    status: Literal["queued", "ringing", "in-progress", "completed", "failed"]
    """통화 상태로 필터링."""

    from_: Union[str, list[str]]
    """발신번호로 필터링. 리스트 시 IN 조건."""

    to: Union[str, list[str]]
    """수신번호로 필터링. 리스트 시 IN 조건."""

    number: Union[str, list[str]]
    """관여 번호로 필터링 (from OR to 매칭). 리스트 시 IN 조건."""

    page: int
    """페이지 번호 (0부터 시작)."""

    page_size: Annotated[int, PropertyInfo(alias="pageSize")]
    """페이지당 항목 수 (기본 20, 최대 100)."""


class CallUpdateParams(TypedDict, total=False):
    """통화 제어 (종료) 요청 파라미터."""

    status: Required[Annotated[Literal["completed"], PropertyInfo(alias="Status")]]
    """변경할 통화 상태. 현재 'completed'만 지원."""
