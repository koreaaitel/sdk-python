from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import ValidationError, field_validator

from .._models import BaseModel


class TransferLeg(BaseModel):
    """전환(담당자 연결) 시도 한 건.

    ``client.calls.get()`` 단건 조회의 ``transfers`` 에만 들어 있다(목록 조회에는 없다).
    sequence 순으로 정렬돼 있다. 시도 하나의 모양이 어긋났다고 통화 조회 전체가 검증 오류로
    실패하면 안 된다 — 필드는 전부 Optional 이고, 값의 타입이 어긋난 시도는 검증 없이 받은 값
    그대로 담는다(``Call._lenient_transfers``).

    담당자가 받기까지 걸린 시간은 ``answered_at - started_at`` 이다. ``connected_at`` 은 warm
    전환에서 연결 전 안내가 끝난 뒤 시각이라 그만큼 늦다.

    Attributes:
        sequence: 이 통화에서 몇 번째 시도인지(1부터). 전환 웹훅의 ``TransferSequence`` 와 같은 값.
        to: 전환 대상 번호 또는 SIP URI.
        mode: blind / warm / refer / refer-attended.
        destination_type: pstn / sip. 2026-07-10 이전 전환은 None.
        status: initiated / ringing / connected / completed / failed / no-answer / busy /
            canceled / interrupted. Literal 로 좁히지 않는다(hangup_cause 와 같은 이유).
        duration: 연결→종료 초(관측값). 과금 초는 billable_duration. 연결되지 못했으면 None.
        billable: 과금 대상 여부. 한 통화에서 최대 한 건만 True.
        billable_duration: 과금 대상 초.
        hangup_cause_q850: 이 시도의 통신망 Q.850 cause.
        sip_response_code: 이 시도의 SIP 응답코드.
        reason_text: 통신망 원본 진단 텍스트(자유 형식 — 분기 조건으로 쓰지 말 것).
        started_at: 시도를 시작한 시각. 2026-07-10 이전 전환은 None.
        answered_at: 대상이 전화를 받은 시각. 연결 전 안내 도중 끝나도 남는다.
            받았는지 판정은 **값이 있으면 받은 것이다** — 안내 도중 끝난 warm 전환은 status 가
            failed 지만 받은 전환이다. 2026-10-13 이전 전환은 받았어도 None 이라, 그 구간만
            status 로 판정한다.
        connected_at: 발신자와 대상이 연결된 시각(안내가 있으면 안내가 끝난 뒤). 연결되지
            못했으면 None.
        ended_at: 이 시도가 끝난 시각.
        timeout: 이 시도에 적용한 대기 한도(초). 2026-10-13 이전 전환은 None.
            refer-attended 는 이미 연결된 상담 통화로 넘겨 기다리는 단계가 없어 항상 None.
    """

    sequence: Optional[int] = None
    to: Optional[str] = None
    mode: Optional[str] = None
    destination_type: Optional[str] = None
    status: Optional[str] = None
    duration: Optional[int] = None
    billable: Optional[bool] = None
    billable_duration: Optional[int] = None
    hangup_cause_q850: Optional[int] = None
    sip_response_code: Optional[int] = None
    reason_text: Optional[str] = None
    started_at: Optional[datetime] = None
    answered_at: Optional[datetime] = None
    connected_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    timeout: Optional[int] = None


class Call(BaseModel):
    """통화 정보를 나타내는 모델.

    아웃바운드 전화 발신 후 반환되거나, 통화 목록/단건 조회 시 반환됩니다.

    Attributes:
        call_id: 통화 고유 식별자 (예: 'CAabcdef1234567890').
        status: 통화 상태. 진행 중은 queued / ringing / in-progress, 종료 상태는
            completed(응답 후 정상 종료) / no-answer(벨은 울렸으나 무응답) /
            busy(통화중) / rejected(수신 거절) / canceled(응답 전 발신 측 취소) /
            failed(시스템·망 오류). completed 만이 실제로 연결된 통화를 의미한다.
        to: 수신 전화번호 또는 SIP URI.
        from_: 발신 전화번호. 발신 시 계정 번호를 쓰고, 계정 번호를 생략한 공용 010 발신에는 선택된 번호를 씁니다.
        presented_from: 공용 010 발신에 사용한 번호. 일반 통화에는 None.
        direction: 통화 방향. outbound 또는 inbound.
        duration: 통화 시간 (초). 통화 중이거나 미연결인 경우 None.
        account_id: 계정 ID.
        date_created: 통화 생성 시각.
        date_updated: 통화 종료 시각. 종료 전이면 None.
        recording_url: 녹음 다운로드 경로(상대). 녹음이 없으면 None.
            예: '/v1/accounts/AC.../recordings/CA...'. 다운로드는
            ``client.accounts(account_id).recordings.download(call_id)`` 사용.
        answered_by: AMD(machine_detection) 결과. machine_detection 을 켠 발신
            통화에만 값이 있으며 human(사람) / machine(자동응답기·음성사서함) /
            unknown(판정 불가). 미사용 시 None.
        hangup_cause: 통화 종료 사유. status 가 왜 그렇게 끝났는지를 구분한다 —
            특히 failed 는 결번·망 오류·시스템 오류를 모두 포함하는 대분류라,
            발신 리스트를 정제하려면 status 가 아니라 이 값을 봐야 한다.
            재시도해도 소용없음: invalid_number(결번) / number_changed /
            incompatible_destination. 재시도 가치 있음: no_answer / user_busy /
            temporary_failure / switching_congestion / no_circuit_available /
            network_out_of_order / destination_out_of_order /
            recovery_on_timer_expire / resource_unavailable. 그 외:
            normal_clearing(정상 종료) / caller_canceled / call_rejected /
            protocol_error / unspecified / app_error·call_stuck(ClawOps 측 오류
            — 재시도 권장) / unknown. 종료 전이거나 사유 미상이면 None.
        hangup_cause_q850: 통신망 Q.850 cause code. 1·5·28=결번, 16=정상해제,
            17=통화중, 18/19/20=무응답, 21=거절, 38=망장애. 사유 미상이면 None.
        sip_response_code: 종료를 유발한 SIP 응답코드. 404=없는 번호, 486=통화중,
            500=망 오류 등. 응답코드 없이 끝났으면 None. 국내 통신망은 실제 사유를
            500 으로 감싸 보내기도 하므로 hangup_cause 가 더 정확하다.
        caller_id_restricted: 수신 통화에서 발신자가 번호 표시제한(`*23#` 등)을 걸고 건
            통화면 True, 아니면 False. 번호는 from_ 에 그대로 있다. 발신 통화와
            2026-09-29 이전의 수신 통화는 None.
        hangup_source: 종료 책임 주체. carrier(통신망) / callee(수신자) /
            caller(발신자) / app·system(ClawOps 측 오류 — 수신자 번호를 정제
            대상에 넣지 말고 재시도할 것).
        transfer_to / transfer_status / transfer_duration / transfer_hangup_cause_q850 /
            transfer_sip_response_code: 전환(담당자 연결) 요약 — 이 통화의 **대표 시도**
            하나에서 나온 값이다. 대표는 끝난 시도(completed / failed / busy / no-answer /
            canceled) 중 completed 우선, 같으면 마지막 시도다. ⚠️ None 이 「전환이 없었다」를
            뜻하지 않는다 — 시도가 진행 중이거나 interrupted 로만 끝났어도 None 이다. 전환 여부는
            transfers 로 판정할 것. transfer_duration 은 연결→종료 관측값(과금 초 아님).
        transfer_reason_text: 통신망 원본 진단 텍스트. ⚠️ 대표 시도가 아니라 **마지막으로 기록된**
            통신망 사유라, 여러 번 시도한 통화에서는 다른 시도의 것일 수 있다. 시도별 사유는
            transfers[].reason_text 를 쓴다.
        transfers: 전환 시도 전체(sequence 순). **``calls.get()`` 단건 조회에만** 있고, 그 밖의
            응답(목록·생성·수정)에서는 None.
    """

    call_id: str
    status: Literal[
        "queued",
        "ringing",
        "in-progress",
        "completed",
        "failed",
        "busy",
        "no-answer",
        "canceled",
        "rejected",
    ]
    to: str
    from_: str
    presented_from: Optional[str] = None
    direction: Literal["outbound", "inbound"]
    duration: Optional[int] = None
    recording_url: Optional[str] = None
    answered_by: Optional[Literal["human", "machine", "unknown"]] = None
    caller_id_restricted: Optional[bool] = None
    # 종료 사유는 Literal 로 좁히지 않는다 — 통신망이 새 cause 를 보내면 서버가 enum 을
    # 넓히는데, 클라이언트가 그때마다 릴리즈되어야 파싱되는 구조는 안 된다.
    hangup_cause: Optional[str] = None
    hangup_cause_q850: Optional[int] = None
    sip_response_code: Optional[int] = None
    hangup_source: Optional[Literal["carrier", "callee", "caller", "app", "system"]] = None
    transfer_to: Optional[str] = None
    transfer_status: Optional[str] = None
    transfer_duration: Optional[int] = None
    transfer_hangup_cause_q850: Optional[int] = None
    transfer_sip_response_code: Optional[int] = None
    transfer_reason_text: Optional[str] = None
    transfers: Optional[List[TransferLeg]] = None
    account_id: str
    date_created: datetime
    date_updated: Optional[datetime] = None

    @field_validator("transfers", mode="before")
    @classmethod
    def _lenient_transfers(cls, v: Any) -> Any:
        # 이 필드가 타입을 갖기 전(0.63.0 까지)에는 extra 로 그냥 받았다. 서버가 시도 하나에 예상과 다른
        # 타입(소수 duration 등)을 보냈다고 통화 전체가 ValidationError 로 실패하면 회귀다 — 그 시도만
        # 검증 없이 받은 값 그대로 담는다(별칭 매핑은 model_construct 도 적용한다).
        if not isinstance(v, list):
            return v
        legs: List[Any] = []
        for item in v:
            if isinstance(item, dict):
                try:
                    legs.append(TransferLeg.model_validate(item))
                except ValidationError:
                    legs.append(TransferLeg.model_construct(**item))
            else:
                legs.append(item)
        return legs


class CallControlResponse(BaseModel):
    """통화 제어 (종료) 응답.

    Attributes:
        call_id: 제어된 통화의 ID.
        status: 변경된 상태 (현재 'completed'만 지원).
    """

    call_id: str
    status: str
