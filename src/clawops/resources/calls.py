from __future__ import annotations

from typing import Literal

from .._resource import AsyncAPIResource, SyncAPIResource
from .._utils import strip_not_given
from ..pagination import AsyncPage, SyncPage
from ..types.call import Call, CallControlResponse
from ..types.call_params import CallContextParam
from ..types.summary import SummaryStatus
from ..types.transcript import TranscriptRequestAccepted, TranscriptStatus


def _call_context_body(ctx: CallContextParam | None) -> dict[str, object] | None:
    """``call_context``\\를 REST 본문의 PascalCase 로 옮긴다.

    파라미터는 snake_case(``instruction``/``variables``)로 받지만 서버는 PascalCase 만
    받는다(스펙상 ``additionalProperties: false`` 라 snake_case 를 그대로 보내면 400).
    ``strip_not_given`` 은 최상위 키만 훑으므로 중첩 객체는 여기서 직접 정리한다.
    """
    if ctx is None:
        return None
    body: dict[str, object] = {"Instruction": ctx["instruction"]}
    variables = ctx.get("variables")
    if variables is not None:
        body["Variables"] = variables
    return body


class Calls(SyncAPIResource):
    """통화(Calls) 리소스. 아웃바운드 전화 발신, 통화 목록 조회, 단건 조회, 통화 제어(종료)."""

    def create(
        self,
        *,
        to: str,
        from_: str | None = None,
        caller_id_mode: Literal["shared-010"] | None = None,
        sdk_agent_id: str | None = None,
        url: str | None = None,
        agent_id: str | None = None,
        call_context: CallContextParam | None = None,
        call_flow_id: str | None = None,
        variables: dict[str, str | float | bool] | None = None,
        status_callback: str | None = None,
        status_callback_event: str | None = None,
        timeout: int | None = None,
        machine_detection: Literal["Enable", "Hangup"] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout_: float | None = None,
    ) -> Call:
        """발신 전화를 생성합니다.

        PSTN 번호로 아웃바운드 전화를 발신합니다.
        일반 발신의 From은 계정에 등록된 번호여야 합니다.
        공용 010 발신은 요청이 접수된 뒤 응답이 유실될 수 있어 자동 재시도를 끕니다.

        통화 처리 대상인 ``url``, ``agent_id``, ``call_flow_id``, ``sdk_agent_id``는 서로 배타적입니다.

        - **VoiceML 모드**: ``url``을 지정하면 VoiceML로 통화를 제어합니다.
        - **매니지드 에이전트 모드**: ``agent_id``를 지정하면 콘솔에서 만든 AI 에이전트가
          통화를 처리합니다.
        - **콜 플로우 모드**: ``call_flow_id``를 지정하면 결정적 ARS 플로우가 진행합니다.
        - **Agent SDK 모드**: ``sdk_agent_id`` 또는 기존 ``from_`` 번호에 연결된 SDK가 통화를 처리합니다.

        Args:
            to: 수신 전화번호.
            from_: 계정에 등록된 발신번호. 공용 010 발신에서 통화 처리 대상을 명시하면 생략할 수 있습니다.
            caller_id_mode: ``"shared-010"``이면 서버가 공용 010 회선을 선택합니다.
            sdk_agent_id: 번호 없이 연결된 자체 호스팅 SDK의 식별자. 공용 010 발신에서 사용합니다.
            url: VoiceML 명령을 반환할 URL. ``agent_id``·``call_flow_id``와 배타.
            agent_id: 콘솔에서 만든 매니지드 에이전트 ID. ``url``·``call_flow_id``와 배타.
            call_context: ``agent_id`` 에이전트의 **이번 통화에만** 적용되는 컨텍스트.
                ``{"instruction": "...", "variables": {...}}`` 형태. 에이전트 자체 설정은
                그대로 두고 이 통화만 다르게 행동시킬 때 쓴다. 같은 에이전트로 동시에 거는
                다른 통화에는 영향이 없다.
            call_flow_id: 콜 플로우(결정적 ARS) ID. ``url``·``agent_id``와 배타.
                ID는 ``client.call_flows.list()``로 조회한다.
            variables: 콜 플로우 시작 변수. 멘트·URL·본문의 ``{{이름}}``이 이 값으로 치환된다.
                ``call_flow_id``와 함께일 때만 쓸 수 있다(단독 지정 시 400).
                ``caller``·``callee``·``recording_url``·``recording_duration``·``http_status``는
                통화 중 자동으로 채워지는 예약 변수라 지정할 수 없다.
            status_callback: 통화 상태 변경 시 POST 요청을 받을 콜백 URL.
            status_callback_event: 수신할 상태 이벤트 목록 (공백 구분).
            timeout: 발신 타임아웃 (초). 기본값: 60.
            machine_detection: 자동응답기/음성사서함 감지(AMD). ``"Enable"``=감지 후 ``AnsweredBy``
                통보(통화 계속), ``"Hangup"``=음성사서함 감지 시 자동 종료. 미설정 시 비활성.
            extra_headers: 추가 HTTP 헤더.
            extra_query: 추가 쿼리 파라미터.
            timeout_: 이 요청의 타임아웃 (초).

        Returns:
            생성된 Call 객체.

        Raises:
            BadRequestError: From 번호가 계정에 등록되지 않았거나, 배타 필드를 함께 지정했거나,
                ``variables``를 ``call_flow_id`` 없이 단독 지정한 경우.
            AuthenticationError: 유효하지 않은 API 키.
            PermissionDeniedError: accountId 불일치.
            InternalServerError: 발신 실패.
            ServiceUnavailableError: ARI 서비스가 준비되지 않음.
        """
        body = strip_not_given(
            {
                "To": to,
                "From": from_,
                "CallerIdMode": caller_id_mode,
                "SdkAgentId": sdk_agent_id,
                "Url": url,
                "AgentId": agent_id,
                "CallContext": _call_context_body(call_context),
                "CallFlowId": call_flow_id,
                "Variables": variables,
                "StatusCallback": status_callback,
                "StatusCallbackEvent": status_callback_event,
                "Timeout": timeout,
                "MachineDetection": machine_detection,
            }
        )
        return self._client._post(
            f"{self._base_path}/calls",
            body=body,
            cast_to=Call,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout_,
            max_retries=0 if caller_id_mode == "shared-010" else None,
        )

    def list(
        self,
        *,
        status: Literal["queued", "ringing", "in-progress", "completed", "failed"] | None = None,
        from_: str | list[str] | None = None,
        to: str | list[str] | None = None,
        number: str | list[str] | None = None,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SyncPage[Call]:
        """통화 목록을 조회합니다.

        계정의 통화 로그를 페이지네이션으로 조회합니다.
        ``auto_paging_iter()``를 사용하면 모든 페이지를 자동으로 순회할 수 있습니다.

        Args:
            status: 통화 상태로 필터링. queued, ringing, in-progress, completed, failed.
            from_: 발신번호로 필터링. 리스트 시 IN 조건.
            to: 수신번호로 필터링. 리스트 시 IN 조건.
            number: 관여 번호로 필터링 (from OR to 매칭). 리스트 시 IN 조건.
            page: 페이지 번호 (0부터 시작, 기본값 0).
            page_size: 페이지당 항목 수 (기본 20, 최대 100).
            extra_headers: 추가 HTTP 헤더.
            extra_query: 추가 쿼리 파라미터.
            timeout: 이 요청의 타임아웃 (초).

        Returns:
            Call 객체의 페이지. ``auto_paging_iter()``로 전체 순회 가능.

        Raises:
            BadRequestError: pageSize 또는 page가 정수가 아닌 경우.
            AuthenticationError: 유효하지 않은 API 키.
            PermissionDeniedError: accountId 불일치.
        """
        query = strip_not_given(
            {"status": status, "from": from_, "to": to, "number": number, "page": page, "pageSize": page_size}
        )
        path = f"{self._base_path}/calls"
        return self._client._get_page(
            path,
            cast_to=Call,
            query=query,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    def get(
        self,
        call_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> Call:
        """특정 통화의 상세 정보를 조회합니다.

        Args:
            call_id: 통화 ID (예: 'CAabcdef1234567890').
            extra_headers: 추가 HTTP 헤더.
            extra_query: 추가 쿼리 파라미터.
            timeout: 이 요청의 타임아웃 (초).

        Returns:
            Call 객체.

        Raises:
            NotFoundError: 해당 통화를 찾을 수 없음.
            AuthenticationError: 유효하지 않은 API 키.
            PermissionDeniedError: accountId 불일치.
        """
        return self._client._get(
            f"{self._base_path}/calls/{call_id}",
            cast_to=Call,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    def get_transcript(
        self,
        call_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> TranscriptStatus:
        """통화 전사 상태를 조회합니다.

        완료된 경우 segment 배열까지 한 번에 반환합니다.

        Args:
            call_id: 통화 ID.

        Returns:
            TranscriptStatus — status 필드로 completed / pending / failed /
            not_requested 구분. completed 일 때 segments 채워짐.

        Raises:
            NotFoundError: 통화가 존재하지 않음.
            PermissionDeniedError: accountId 불일치.
        """
        return self._client._get(
            f"{self._base_path}/calls/{call_id}/transcript",
            cast_to=TranscriptStatus,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    def request_transcript(
        self,
        call_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> TranscriptRequestAccepted:
        """특정 통화 1 건에 대해 전사를 명시 요청합니다.

        조직 설정 "통화 받아쓰기" 가 꺼져 있어도 해당 통화만 전사됩니다.
        재실행 금지 — 이미 요청된 통화는 ConflictError 발생. 시스템 레벨
        트리거 실패(stage=trigger) 만 재시도 가능합니다. 전사된 오디오
        길이만큼 사용량 기반으로 과금됩니다.

        Args:
            call_id: 통화 ID.

        Returns:
            TranscriptRequestAccepted — {"status": "pending", "call_id": ...}.

        Raises:
            BadRequestError: 녹음 없음 (400).
            NotFoundError: 통화 없음 (404).
            PermissionDeniedError: accountId 불일치 (403).
            ConflictError: 이미 요청됨 — completed / pending / failed (409).
        """
        return self._client._post(
            f"{self._base_path}/calls/{call_id}/transcript",
            cast_to=TranscriptRequestAccepted,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    def get_summary(
        self,
        call_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SummaryStatus:
        """통화 요약 상태를 조회합니다.

        transcript 가 완료된 통화에 대해 자동 생성된 LLM 구조화 요약 결과를 반환합니다.

        Args:
            call_id: 통화 ID.

        Returns:
            SummaryStatus — status 필드로 completed / pending / failed /
            not_requested 구분. completed 일 때 result_json 채워짐.

        Raises:
            NotFoundError: 통화가 존재하지 않음.
            PermissionDeniedError: accountId 불일치.
        """
        return self._client._get(
            f"{self._base_path}/calls/{call_id}/summary",
            cast_to=SummaryStatus,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    def update(
        self,
        call_id: str,
        *,
        status: Literal["completed"] = "completed",
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> CallControlResponse:
        """진행 중인 통화를 제어(종료)합니다.

        현재 Status='completed'(통화 종료)만 지원합니다.

        Args:
            call_id: 종료할 통화의 ID.
            status: 변경할 상태. 현재 'completed'만 지원.
            extra_headers: 추가 HTTP 헤더.
            extra_query: 추가 쿼리 파라미터.
            timeout: 이 요청의 타임아웃 (초).

        Returns:
            CallControlResponse 객체.

        Raises:
            BadRequestError: 지원되지 않는 Status 값.
            NotFoundError: 해당 통화를 찾을 수 없음.
        """
        return self._client._post(
            f"{self._base_path}/calls/{call_id}",
            body={"Status": status},
            cast_to=CallControlResponse,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )


class AsyncCalls(AsyncAPIResource):
    """통화(Calls) 비동기 리소스. Calls의 async 버전."""

    async def create(
        self,
        *,
        to: str,
        from_: str | None = None,
        caller_id_mode: Literal["shared-010"] | None = None,
        sdk_agent_id: str | None = None,
        url: str | None = None,
        agent_id: str | None = None,
        call_context: CallContextParam | None = None,
        call_flow_id: str | None = None,
        variables: dict[str, str | float | bool] | None = None,
        status_callback: str | None = None,
        status_callback_event: str | None = None,
        timeout: int | None = None,
        machine_detection: Literal["Enable", "Hangup"] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout_: float | None = None,
    ) -> Call:
        """발신 전화를 비동기로 생성합니다. 자세한 내용은 Calls.create를 참고하세요."""
        body = strip_not_given(
            {
                "To": to,
                "From": from_,
                "CallerIdMode": caller_id_mode,
                "SdkAgentId": sdk_agent_id,
                "Url": url,
                "AgentId": agent_id,
                "CallContext": _call_context_body(call_context),
                "CallFlowId": call_flow_id,
                "Variables": variables,
                "StatusCallback": status_callback,
                "StatusCallbackEvent": status_callback_event,
                "Timeout": timeout,
                "MachineDetection": machine_detection,
            }
        )
        return await self._client._post(
            f"{self._base_path}/calls",
            body=body,
            cast_to=Call,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout_,
            max_retries=0 if caller_id_mode == "shared-010" else None,
        )

    async def list(
        self,
        *,
        status: Literal["queued", "ringing", "in-progress", "completed", "failed"] | None = None,
        from_: str | list[str] | None = None,
        to: str | list[str] | None = None,
        number: str | list[str] | None = None,
        page: int | None = None,
        page_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> AsyncPage[Call]:
        """통화 목록을 비동기로 조회합니다."""
        query = strip_not_given(
            {"status": status, "from": from_, "to": to, "number": number, "page": page, "pageSize": page_size}
        )
        path = f"{self._base_path}/calls"
        return await self._client._get_page(
            path,
            cast_to=Call,
            query=query,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    async def get(
        self,
        call_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> Call:
        """특정 통화를 비동기로 조회합니다."""
        return await self._client._get(
            f"{self._base_path}/calls/{call_id}",
            cast_to=Call,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    async def get_transcript(
        self,
        call_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> TranscriptStatus:
        """통화 전사 상태를 비동기 조회. 자세한 내용은 Calls.get_transcript 참고."""
        return await self._client._get(
            f"{self._base_path}/calls/{call_id}/transcript",
            cast_to=TranscriptStatus,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    async def request_transcript(
        self,
        call_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> TranscriptRequestAccepted:
        """특정 통화 1 건에 대해 전사를 비동기 명시 요청.
        자세한 내용은 Calls.request_transcript 참고."""
        return await self._client._post(
            f"{self._base_path}/calls/{call_id}/transcript",
            cast_to=TranscriptRequestAccepted,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    async def get_summary(
        self,
        call_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SummaryStatus:
        """통화 요약 상태를 비동기 조회. 자세한 내용은 Calls.get_summary 참고."""
        return await self._client._get(
            f"{self._base_path}/calls/{call_id}/summary",
            cast_to=SummaryStatus,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )

    async def update(
        self,
        call_id: str,
        *,
        status: Literal["completed"] = "completed",
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> CallControlResponse:
        """진행 중인 통화를 비동기로 종료합니다."""
        return await self._client._post(
            f"{self._base_path}/calls/{call_id}",
            body={"Status": status},
            cast_to=CallControlResponse,
            extra_headers=extra_headers,
            extra_query=extra_query,
            timeout=timeout,
        )
