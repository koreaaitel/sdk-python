from datetime import datetime, timedelta

from clawops.types.call import Call, CallControlResponse, TransferLeg
from clawops.types.number import PhoneNumber, NumberListItem
from clawops.types.shared import PaginationMeta


def test_call_from_api_response():
    data = {
        "callId": "CAabcdef1234567890abcdef1234567890",
        "status": "queued",
        "to": "01012345678",
        "from": "07052358010",
        "direction": "outbound",
        "duration": None,
        "accountId": "AC1a2b3c4d",
        "dateCreated": "2025-06-01T12:00:00Z",
        "dateUpdated": None,
    }
    call = Call.model_validate(data)
    assert call.call_id == "CAabcdef1234567890abcdef1234567890"
    assert call.status == "queued"
    assert call.to == "01012345678"
    assert call.from_ == "07052358010"
    assert call.direction == "outbound"
    assert call.duration is None
    assert call.account_id == "AC1a2b3c4d"
    assert isinstance(call.date_created, datetime)
    assert call.date_updated is None


def test_call_hangup_cause_from_api_response():
    """실패 통화의 종료 사유 — 결번을 재시도 대상과 구분하는 값."""
    data = {
        "callId": "CA14ad61795d28ba036a383f66565376b4",
        "status": "failed",
        "to": "07080588491",
        "from": "07052361088",
        "direction": "outbound",
        "duration": 0,
        "accountId": "AC1a2b3c4d",
        "dateCreated": "2026-07-30T17:18:15Z",
        "dateUpdated": "2026-07-30T17:18:15Z",
        "hangupCause": "invalid_number",
        "hangupCauseQ850": 1,
        "sipResponseCode": 404,
        "hangupSource": "carrier",
    }
    call = Call.model_validate(data)
    assert call.hangup_cause == "invalid_number"
    assert call.hangup_cause_q850 == 1
    assert call.sip_response_code == 404
    assert call.hangup_source == "carrier"


def test_call_hangup_cause_absent_is_none():
    """종료 전이거나 사유 미상인 통화는 네 필드 모두 None (하위호환)."""
    data = {
        "callId": "CA123",
        "status": "in-progress",
        "to": "01012345678",
        "from": "07052358010",
        "direction": "outbound",
        "accountId": "AC1a2b3c4d",
        "dateCreated": "2026-07-30T17:18:15Z",
    }
    call = Call.model_validate(data)
    assert call.hangup_cause is None
    assert call.hangup_cause_q850 is None
    assert call.sip_response_code is None
    assert call.hangup_source is None


def test_call_unknown_hangup_cause_is_accepted():
    """서버가 새 cause 를 보내도 파싱은 깨지지 않는다 (Literal 로 좁히지 않은 이유)."""
    data = {
        "callId": "CA123",
        "status": "failed",
        "to": "01012345678",
        "from": "07052358010",
        "direction": "outbound",
        "accountId": "AC1a2b3c4d",
        "dateCreated": "2026-07-30T17:18:15Z",
        "hangupCause": "some_future_cause",
        "hangupCauseQ850": 255,
    }
    call = Call.model_validate(data)
    assert call.hangup_cause == "some_future_cause"
    assert call.hangup_cause_q850 == 255


def test_call_control_response():
    data = {"callId": "CA123", "status": "completed"}
    resp = CallControlResponse.model_validate(data)
    assert resp.call_id == "CA123"
    assert resp.status == "completed"


def test_phone_number_from_api():
    data = {"number": "07012340001"}
    num = PhoneNumber.model_validate(data)
    assert num.number == "07012340001"


def test_number_list_item():
    data = {
        "number": "07012340001",
        "webhookUrl": "https://my-app.com/voice",
        "createdAt": "2025-06-01T12:00:00Z",
    }
    item = NumberListItem.model_validate(data)
    assert item.number == "07012340001"
    assert item.webhook_url == "https://my-app.com/voice"


def test_pagination_meta():
    data = {"total": 100, "page": 2, "pageSize": 20}
    meta = PaginationMeta.model_validate(data)
    assert meta.total == 100
    assert meta.page == 2
    assert meta.page_size == 20


# ── 전환(담당자 연결) 필드 — koreaaitel/clawops#1215 ──────────────────────────
# 서버 응답 모양: warm 전환이 안내 뒤에 연결된 시도 하나(값은 가짜).
_TRANSFER_CALL = {
    "callId": "CA00000000000000000000000000000001",
    "status": "completed",
    "to": "01000000000",
    "from": "07012345678",
    "direction": "outbound",
    "duration": 62,
    "accountId": "AC123",
    "dateCreated": "2026-10-13T07:20:13Z",
    "transferTo": "0212345678",
    "transferStatus": "completed",
    "transferDuration": 2,
    "transferHangupCauseQ850": None,
    "transferSipResponseCode": None,
    "transferReasonText": None,
    "transfers": [
        {
            "sequence": 1,
            "to": "0212345678",
            "mode": "warm",
            "destinationType": "pstn",
            "status": "completed",
            "duration": 2,
            "billable": True,
            "billableDuration": 2,
            "hangupCauseQ850": None,
            "sipResponseCode": None,
            "reasonText": None,
            "startedAt": "2026-10-13T07:21:05.760Z",
            "answeredAt": "2026-10-13T07:21:11.312Z",
            "connectedAt": "2026-10-13T07:21:19.403Z",
            "endedAt": "2026-10-13T07:21:20.989Z",
            "timeout": 30,
        }
    ],
}


def test_call_transfer_summary_and_legs():
    call = Call.model_validate(_TRANSFER_CALL)
    assert call.transfer_status == "completed"
    assert call.transfer_to == "0212345678"
    assert call.transfers is not None
    leg = call.transfers[0]
    assert isinstance(leg, TransferLeg)
    # 응답 시간 = answered_at - started_at (connected_at 은 안내 뒤라 늦다).
    assert leg.answered_at is not None and leg.started_at is not None
    assert leg.answered_at - leg.started_at == timedelta(milliseconds=5552)
    assert leg.timeout == 30


# 실제 서버는 이전 시도의 값도 키를 빼지 않고 None(null)으로 보낸다.
def test_transfer_leg_before_answered_at_existed_is_none():
    data = {
        **_TRANSFER_CALL,
        "transfers": [
            {
                "sequence": 1,
                "to": "0212345678",
                "mode": "blind",
                "destinationType": "pstn",
                "status": "no-answer",
                "duration": None,
                "billable": False,
                "billableDuration": None,
                "hangupCauseQ850": 19,
                "sipResponseCode": 480,
                "reasonText": None,
                "startedAt": "2026-08-27T04:11:02.000Z",
                "answeredAt": None,
                "connectedAt": None,
                "endedAt": "2026-08-27T04:11:32.000Z",
                "timeout": None,
            }
        ],
    }
    leg = Call.model_validate(data).transfers[0]  # type: ignore[index]
    assert leg.answered_at is None
    assert leg.timeout is None
    assert leg.sip_response_code == 480


# 시도 하나의 모양이 어긋났다고 통화 전체 조회가 실패하면 안 된다.
def test_transfer_leg_missing_fields_does_not_break_call():
    call = Call.model_validate({**_TRANSFER_CALL, "transfers": [{"sequence": None, "status": "completed"}]})
    assert call.transfers is not None
    assert call.transfers[0].status == "completed"


# 목록 조회는 transfers 를 싣지 않는다.
def test_call_list_row_without_transfers():
    data = {k: v for k, v in _TRANSFER_CALL.items() if k != "transfers"}
    call = Call.model_validate({**data, "transferStatus": "no-answer", "transferDuration": None})
    assert call.transfers is None
    assert call.transfer_status == "no-answer"


# 키가 빠진 게 아니라 **타입이 어긋난** 시도도 통화 전체를 실패시키지 않는다(0.63.0 까지는 extra 로 받았다).
def test_transfer_leg_wrong_types_keep_raw_values():
    call = Call.model_validate(
        {**_TRANSFER_CALL, "transfers": [{"sequence": 1, "duration": 2.5, "billable": "yes", "status": "completed"}]}
    )
    assert call.status == "completed"
    assert call.transfers is not None
    leg = call.transfers[0]
    assert leg.status == "completed"
    assert leg.duration == 2.5  # 검증 없이 받은 값 그대로
