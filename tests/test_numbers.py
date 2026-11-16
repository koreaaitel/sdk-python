import json
import httpx
import pytest
import respx

from clawops._base_client import SyncAPIClient
from clawops.resources.numbers import Numbers
from clawops.types.number import PhoneNumber, NumberListItem, NumberUpdateResponse

BASE = "https://api.koreaaitel.com"
ACCOUNT = "AC1a2b3c4d"
NUMBERS_PATH = f"/v1/accounts/{ACCOUNT}/numbers"

@pytest.fixture
def client():
    c = SyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)
    yield c
    c.close()

@pytest.fixture
def numbers(client):
    return Numbers(client=client, account_id=ACCOUNT)

class TestNumbersCreate:
    @respx.mock
    def test_create_pool(self, numbers):
        respx.post(f"{BASE}{NUMBERS_PATH}").mock(return_value=httpx.Response(201, json={"number": "07012340001"}))
        num = numbers.create()
        assert isinstance(num, PhoneNumber)
        assert num.number == "07012340001"

    @respx.mock
    def test_create_with_webhook_and_status_callback(self, numbers):
        route = respx.post(f"{BASE}{NUMBERS_PATH}").mock(
            return_value=httpx.Response(201, json={"number": "07012340001"})
        )
        numbers.create(
            webhook_url="https://my-app.com/voice",
            webhook_method="POST",
            webhook_headers={"X-Webhook-Token": "abc123"},
            status_callback="https://my-app.com/call-status",
            status_callback_events="initiated ringing answered completed transfer",
        )
        body = json.loads(route.calls[0].request.content)
        assert body == {
            "webhookUrl": "https://my-app.com/voice",
            "webhookMethod": "POST",
            "webhookHeaders": {"X-Webhook-Token": "abc123"},
            "statusCallback": "https://my-app.com/call-status",
            "statusCallbackEvents": "initiated ringing answered completed transfer",
        }

class TestNumbersList:
    @respx.mock
    def test_list(self, numbers):
        respx.get(f"{BASE}{NUMBERS_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [
                {"number": "07012340001", "webhookUrl": None, "webhookMethod": "POST", "createdAt": "2025-06-01T12:00:00Z"},
            ]
        }))
        result = numbers.list()
        assert len(result) == 1
        assert isinstance(result[0], NumberListItem)

    @respx.mock
    def test_list_accepts_every_routing_type(self, numbers):
        """agent·callflow·forward 로 라우팅된 번호가 섞여도 목록 조회가 깨지지 않는다.

        0.40.0 까지는 routing_type 이 webhook/sip/softphone 으로 좁혀져 있어, 에이전트에
        연결된 번호 하나가 numbers.list() 전체를 ValidationError 로 실패시켰다.
        """
        respx.get(f"{BASE}{NUMBERS_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [
                {"number": "07012340001", "routingType": "agent", "agentId": "AG7c2f9b1e4a6d"},
                {"number": "07012340002", "routingType": "callflow", "callFlowId": "CF41b8e07d9c25"},
                {"number": "07012340003", "routingType": "forward", "forwardTo": "07012340001"},
                {"number": "15551234", "routingType": "forward", "numberType": "representative"},
                {"number": "07012340004", "routingType": "webhook", "dictionaryId": "DC6b41e8f0a92c"},
                # 서버가 앞으로 라우팅을 추가해도 기존 SDK 가 깨지지 않아야 한다.
                {"number": "07012340005", "routingType": "some-future-routing"},
            ]
        }))
        result = numbers.list()
        assert [n.routing_type for n in result] == [
            "agent", "callflow", "forward", "forward", "webhook", "some-future-routing",
        ]
        assert result[0].agent_id == "AG7c2f9b1e4a6d"
        assert result[1].call_flow_id == "CF41b8e07d9c25"
        assert result[2].forward_to == "07012340001"
        assert result[3].number_type == "representative"
        assert result[4].dictionary_id == "DC6b41e8f0a92c"

class TestNumbersUpdate:
    @respx.mock
    def test_update_webhook(self, numbers):
        respx.put(f"{BASE}{NUMBERS_PATH}/07012340001").mock(return_value=httpx.Response(200, json={
            "number": "07012340001", "webhookUrl": "https://new.com", "webhookMethod": "POST", "createdAt": "2025-06-01T12:00:00Z",
        }))
        result = numbers.update("07012340001", webhook_url="https://new.com")
        assert isinstance(result, NumberUpdateResponse)
        assert result.webhook_url == "https://new.com"

    @respx.mock
    def test_update_routing_to_agent(self, numbers):
        route = respx.put(f"{BASE}{NUMBERS_PATH}/07012340001").mock(return_value=httpx.Response(200, json={
            "number": "07012340001", "routingType": "agent", "agentId": "AG7c2f9b1e4a6d",
        }))
        result = numbers.update(
            "07012340001",
            routing_type="agent",
            agent_id="AG7c2f9b1e4a6d",
            call_context_url="https://my-app.com/call-context",
        )
        body = json.loads(route.calls[0].request.content)
        assert body == {
            "routingType": "agent",
            "agentId": "AG7c2f9b1e4a6d",
            "callContextUrl": "https://my-app.com/call-context",
        }
        assert result.routing_type == "agent"
        assert result.agent_id == "AG7c2f9b1e4a6d"

    @respx.mock
    def test_update_routing_to_callflow_and_forward(self, numbers):
        route = respx.put(f"{BASE}{NUMBERS_PATH}/07012340001").mock(return_value=httpx.Response(200, json={
            "number": "07012340001", "routingType": "callflow", "callFlowId": "CF41b8e07d9c25",
        }))
        numbers.update("07012340001", routing_type="callflow", call_flow_id="CF41b8e07d9c25")
        assert json.loads(route.calls[0].request.content) == {
            "routingType": "callflow", "callFlowId": "CF41b8e07d9c25",
        }

        numbers.update("07012340001", routing_type="forward", forward_to="07012340002")
        assert json.loads(route.calls[1].request.content) == {
            "routingType": "forward", "forwardTo": "07012340002",
        }

    @respx.mock
    def test_update_dictionary_and_status_callback(self, numbers):
        route = respx.put(f"{BASE}{NUMBERS_PATH}/07012340001").mock(return_value=httpx.Response(200, json={
            "number": "07012340001", "dictionaryId": "DC6b41e8f0a92c",
        }))
        numbers.update(
            "07012340001",
            dictionary_id="DC6b41e8f0a92c",
            status_callback="https://my-app.com/call-status",
            status_callback_events="initiated ringing answered completed",
        )
        assert json.loads(route.calls[0].request.content) == {
            "statusCallback": "https://my-app.com/call-status",
            "statusCallbackEvents": "initiated ringing answered completed",
            "dictionaryId": "DC6b41e8f0a92c",
        }

class TestNumbersDelete:
    @respx.mock
    def test_delete(self, numbers):
        respx.delete(f"{BASE}{NUMBERS_PATH}/07012340001").mock(return_value=httpx.Response(204))
        result = numbers.delete("07012340001")
        assert result is None
