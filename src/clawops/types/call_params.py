from __future__ import annotations

from typing import Annotated, Literal, Union

from typing_extensions import Required, TypedDict

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


class CallCreateParams(TypedDict, total=False):
    """발신 전화 생성 요청 파라미터.

    통화 처리 방식인 ``url``\\, ``agent_id``\\, ``call_flow_id``\\, ``sdk_agent_id``\\는 서로 배타적입니다.

    - **VoiceML 모드**: ``url``\\을 지정하면 VoiceML로 통화를 제어합니다.
    - **매니지드 에이전트 모드**: ``agent_id``\\를 지정하면 콘솔에서 만든 AI 에이전트가
      통화를 처리합니다. ``call_context``\\로 이번 통화만의 지시를 덧붙일 수 있습니다.
    - **콜 플로우 모드**: ``call_flow_id``\\를 지정하면 결정적 ARS 플로우가 통화를
      진행합니다. ``variables``\\로 시작 변수를 넘깁니다.
    - **Agent SDK 모드**: 기존 ``from_`` 번호 또는 ``sdk_agent_id``\\에 연결된 SDK가 통화를 처리합니다.
    """

    to: Required[Annotated[str, PropertyInfo(alias="To")]]
    """수신 대상. 전화번호(PSTN) 또는 sip: URI(내선)."""

    from_: Annotated[str, PropertyInfo(alias="From")]
    """계정에 등록된 발신번호. 공용 010 발신에서 통화 처리 대상을 명시하면 생략할 수 있습니다."""

    caller_id_mode: Annotated[Literal["shared-010"], PropertyInfo(alias="CallerIdMode")]
    """서버가 사용 가능한 공용 010 회선을 고르는 발신 방식."""

    sdk_agent_id: Annotated[str, PropertyInfo(alias="SdkAgentId")]
    """번호 없이 연결된 자체 호스팅 SDK의 식별자. 공용 010 발신에서 사용합니다."""

    url: Annotated[str, PropertyInfo(alias="Url")]
    """통화 연결 시 VoiceML 명령을 반환할 URL. ``agent_id``\\·``call_flow_id``\\와 배타."""

    agent_id: Annotated[str, PropertyInfo(alias="AgentId")]
    """콘솔에서 만든 매니지드 에이전트 ID. ``url``\\·``call_flow_id``\\와 배타."""

    call_context: Annotated[CallContextParam, PropertyInfo(alias="CallContext")]
    """``agent_id`` 에이전트의 이번 통화에만 적용되는 컨텍스트."""

    call_flow_id: Annotated[str, PropertyInfo(alias="CallFlowId")]
    """콜 플로우(결정적 ARS) ID. ``url``\\·``agent_id``\\와 배타."""

    variables: Annotated[dict[str, Union[str, float, bool]], PropertyInfo(alias="Variables")]
    """콜 플로우 시작 변수. ``call_flow_id``\\와 함께일 때만 사용할 수 있습니다."""

    status_callback: Annotated[str, PropertyInfo(alias="StatusCallback")]
    """통화 상태 변경 시 POST 요청을 받을 콜백 URL."""

    status_callback_event: Annotated[str, PropertyInfo(alias="StatusCallbackEvent")]
    """수신할 상태 이벤트 목록 (공백 구분)."""

    timeout: Annotated[int, PropertyInfo(alias="Timeout")]
    """발신 타임아웃 (초). 기본값: 60."""

    machine_detection: Annotated[Literal["Enable", "Hangup"], PropertyInfo(alias="MachineDetection")]
    """자동응답기/음성사서함 감지(AMD)."""


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
