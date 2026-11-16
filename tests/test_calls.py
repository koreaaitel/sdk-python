import json
import httpx
import pytest
import respx

from clawops._base_client import SyncAPIClient
from clawops.resources.calls import Calls
from clawops.types.call import Call, CallControlResponse
from clawops.types.transcript import TranscriptRequestAccepted, TranscriptStatus

BASE = "https://api.koreaaitel.com"
ACCOUNT = "AC1a2b3c4d"
CALLS_PATH = f"/v1/accounts/{ACCOUNT}/calls"

CALL_JSON = {
    "callId": "CA0123456789abcdef0123456789abcdef",
    "status": "queued", "to": "01012345678", "from": "07052358010",
    "direction": "outbound", "duration": None, "accountId": "AC1a2b3c4d",
    "dateCreated": "2025-06-01T12:00:00Z", "dateUpdated": None,
}

@pytest.fixture
def client():
    c = SyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)
    yield c
    c.close()

@pytest.fixture
def calls(client):
    return Calls(client=client, account_id=ACCOUNT)

class TestCallsCreate:
    @respx.mock
    def test_create_call(self, calls):
        respx.post(f"{BASE}{CALLS_PATH}").mock(return_value=httpx.Response(201, json=CALL_JSON))
        call = calls.create(to="01012345678", from_="07052358010", url="https://my-app.com/twiml")
        assert isinstance(call, Call)
        assert call.call_id == "CA0123456789abcdef0123456789abcdef"
        assert call.from_ == "07052358010"

    @respx.mock
    def test_create_with_callback(self, calls):
        route = respx.post(f"{BASE}{CALLS_PATH}").mock(return_value=httpx.Response(201, json=CALL_JSON))
        calls.create(to="010", from_="070", url="https://t.com",
                     status_callback="https://my-app.com/status", status_callback_event="initiated completed")
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["StatusCallback"] == "https://my-app.com/status"

    @respx.mock
    def test_create_with_agent_id(self, calls):
        route = respx.post(f"{BASE}{CALLS_PATH}").mock(return_value=httpx.Response(201, json=CALL_JSON))
        calls.create(to="01012345678", from_="07052358010", agent_id="cmagent123")
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["AgentId"] == "cmagent123"
        # 배타 필드는 보내지 않은 채로 남아야 한다 — 빈 값이라도 실리면 서버가 400 으로 막는다.
        assert "Url" not in parsed
        assert "CallFlowId" not in parsed

    @respx.mock
    def test_create_with_call_context_uses_pascal_case(self, calls):
        """call_context 는 snake_case 로 받지만 본문은 PascalCase 여야 한다.

        서버 스펙이 CallContext 에 additionalProperties:false 라, snake_case 를 그대로
        흘려보내면 400 이 난다. 변환이 빠지면 이 테스트가 먼저 깨진다.
        """
        route = respx.post(f"{BASE}{CALLS_PATH}").mock(return_value=httpx.Response(201, json=CALL_JSON))
        calls.create(
            to="01012345678",
            from_="07052358010",
            agent_id="cmagent123",
            call_context={"instruction": "예약 확인만 하고 끊어라", "variables": {"orderId": "A-1234"}},
        )
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["CallContext"] == {
            "Instruction": "예약 확인만 하고 끊어라",
            "Variables": {"orderId": "A-1234"},
        }

    @respx.mock
    def test_create_with_call_context_omits_absent_variables(self, calls):
        route = respx.post(f"{BASE}{CALLS_PATH}").mock(return_value=httpx.Response(201, json=CALL_JSON))
        calls.create(to="010", from_="070", agent_id="cmagent123",
                     call_context={"instruction": "본인확인만 하라"})
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["CallContext"] == {"Instruction": "본인확인만 하라"}

    @respx.mock
    def test_create_with_call_flow_and_variables(self, calls):
        route = respx.post(f"{BASE}{CALLS_PATH}").mock(return_value=httpx.Response(201, json=CALL_JSON))
        calls.create(to="01012345678", from_="07052358010",
                     call_flow_id="cmryw3ycm000001s6on0kp9a8",
                     variables={"name": "홍길동", "orderId": "A-1234"})
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["CallFlowId"] == "cmryw3ycm000001s6on0kp9a8"
        assert parsed["Variables"] == {"name": "홍길동", "orderId": "A-1234"}

    @respx.mock
    def test_create_agent_sdk_mode_sends_no_routing_field(self, calls):
        """셋 다 생략 = Agent SDK 모드. 라우팅 키가 하나도 실리면 안 된다."""
        route = respx.post(f"{BASE}{CALLS_PATH}").mock(return_value=httpx.Response(201, json=CALL_JSON))
        calls.create(to="01012345678", from_="07052358010")
        parsed = json.loads(route.calls[0].request.content)
        assert set(parsed) == {"To", "From"}


class TestCallsList:
    @respx.mock
    def test_list_calls(self, calls):
        respx.get(f"{BASE}{CALLS_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [CALL_JSON], "meta": {"total": 1, "page": 0, "pageSize": 20},
        }))
        page = calls.list()
        items = list(page)
        assert len(items) == 1
        assert isinstance(items[0], Call)

    @respx.mock
    def test_list_with_filters(self, calls):
        route = respx.get(f"{BASE}{CALLS_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [], "meta": {"total": 0, "page": 0, "pageSize": 10},
        }))
        calls.list(status="completed", page=0, page_size=10)
        url = str(route.calls[0].request.url)
        assert "status=completed" in url
        assert "pageSize=10" in url

class TestCallsGet:
    @respx.mock
    def test_get_call(self, calls):
        cid = "CA0123456789abcdef0123456789abcdef"
        respx.get(f"{BASE}{CALLS_PATH}/{cid}").mock(return_value=httpx.Response(200, json=CALL_JSON))
        call = calls.get(cid)
        assert call.call_id == cid

class TestCallsUpdate:
    @respx.mock
    def test_update_completed(self, calls):
        cid = "CA0123456789abcdef0123456789abcdef"
        respx.post(f"{BASE}{CALLS_PATH}/{cid}").mock(
            return_value=httpx.Response(200, json={"callId": cid, "status": "completed"}))
        result = calls.update(cid, status="completed")
        assert isinstance(result, CallControlResponse)
        assert result.status == "completed"


class TestCallsTranscript:
    CID = "CAd6d8debfef62953acc35e37f3068745a"

    @respx.mock
    def test_get_transcript_completed(self, calls):
        respx.get(f"{BASE}{CALLS_PATH}/{self.CID}/transcript").mock(
            return_value=httpx.Response(200, json={
                "status": "completed",
                "callId": self.CID,
                "segmentCount": 2,
                "segments": [
                    {"speaker": "AGENT", "start": 0.0, "end": 1.2, "text": "안녕하세요."},
                    {"speaker": "CUSTOMER", "start": 1.5, "end": 2.8, "text": "네."},
                ],
            })
        )
        r = calls.get_transcript(self.CID)
        assert isinstance(r, TranscriptStatus)
        assert r.status == "completed"
        assert r.segment_count == 2
        assert r.segments and r.segments[0].speaker == "AGENT"

    @respx.mock
    def test_get_transcript_speaker_n(self, calls):
        """2026-08 이후 전사는 `speaker_0`·`speaker_1`… 을 보낸다.

        segments 가 리스트라 **조각 하나가 응답 전체를 죽인다** — 닫힌 Literal 이던 시절
        최근 전사가 전부 ValidationError 였다. 옛 전사의 AGENT/CUSTOMER 도 함께 온다.
        """
        respx.get(f"{BASE}{CALLS_PATH}/{self.CID}/transcript").mock(
            return_value=httpx.Response(200, json={
                "status": "completed",
                "callId": self.CID,
                "segmentCount": 3,
                "segments": [
                    {"speaker": "speaker_0", "start": 0.0, "end": 1.2, "text": "안녕하세요."},
                    {"speaker": "speaker_1", "start": 1.5, "end": 2.8, "text": "네."},
                    {"speaker": "AGENT", "start": 3.0, "end": 4.0, "text": "옛 전사입니다."},
                ],
            })
        )
        r = calls.get_transcript(self.CID)
        assert r.segments and [s.speaker for s in r.segments] == ["speaker_0", "speaker_1", "AGENT"]

    @respx.mock
    def test_get_transcript_failed_stage_beyond_known(self, calls):
        """실패 단계는 서버 코드가 만든다 — 영구 실패는 예외 객체의 속성을 그대로 싣는다.

        ⛔ 여기가 닫혀 있으면 **전사가 실패했을 때 그 이유를 물으면 던진다.**
        고객이 가장 답을 필요로 하는 순간이다.
        """
        respx.get(f"{BASE}{CALLS_PATH}/{self.CID}/transcript").mock(
            return_value=httpx.Response(200, json={
                "status": "failed",
                "stage": "transcription",
                "error": "transcription_failed",
            })
        )
        r = calls.get_transcript(self.CID)
        assert r.stage == "transcription"

    @respx.mock
    def test_get_transcript_pending(self, calls):
        respx.get(f"{BASE}{CALLS_PATH}/{self.CID}/transcript").mock(
            return_value=httpx.Response(200, json={
                "status": "pending",
                "startedAt": "2026-04-23T08:33:00Z",
            })
        )
        r = calls.get_transcript(self.CID)
        assert r.status == "pending"
        assert r.started_at is not None

    @respx.mock
    def test_get_transcript_failed(self, calls):
        respx.get(f"{BASE}{CALLS_PATH}/{self.CID}/transcript").mock(
            return_value=httpx.Response(200, json={
                "status": "failed", "stage": "runtime", "error": "boom",
            })
        )
        r = calls.get_transcript(self.CID)
        assert r.status == "failed"
        assert r.stage == "runtime"
        assert r.error == "boom"

    @respx.mock
    def test_get_transcript_not_requested(self, calls):
        respx.get(f"{BASE}{CALLS_PATH}/{self.CID}/transcript").mock(
            return_value=httpx.Response(200, json={"status": "not_requested"})
        )
        r = calls.get_transcript(self.CID)
        assert r.status == "not_requested"

    @respx.mock
    def test_request_transcript_accepted(self, calls):
        respx.post(f"{BASE}{CALLS_PATH}/{self.CID}/transcript").mock(
            return_value=httpx.Response(202, json={"status": "pending", "callId": self.CID})
        )
        r = calls.request_transcript(self.CID)
        assert isinstance(r, TranscriptRequestAccepted)
        assert r.status == "pending"
        assert r.call_id == self.CID
