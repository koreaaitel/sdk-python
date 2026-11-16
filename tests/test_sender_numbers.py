"""문자 발신번호 리소스(clawops#1489)와 numbers.create() 의 sms_sender."""
import json

import httpx
import pytest
import respx

from clawops._base_client import AsyncAPIClient, SyncAPIClient
from clawops._exceptions import ConflictError
from clawops.resources.numbers import Numbers
from clawops.resources.sender_numbers import AsyncSenderNumbers, SenderNumbers
from clawops.types.number import ProvisionedPhoneNumber
from clawops.types.sender_number import SenderNumber, SenderNumberList

BASE = "https://api.koreaaitel.com"
ACCOUNT = "AC1a2b3c4d"
PATH = f"/v1/accounts/{ACCOUNT}/sender-numbers"

SAMPLE = {
    "id": "sn_1",
    "number": "07052361008",
    "type": "070",
    "verificationMethod": "own_provisioned",
    "status": "approved",
    "createdAt": "2026-10-20T07:02:11.000Z",
}
LIST = {
    "senderNumbers": [SAMPLE],
    "grace": [{"number": "07052753809", "expiresAt": "2026-11-03T14:59:59.000Z"}],
    "limit": {"used": 1, "limit": 5, "reached": False},
}


@pytest.fixture
def client():
    c = SyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)
    yield c
    c.close()


@pytest.fixture
def senders(client):
    return SenderNumbers(client=client, account_id=ACCOUNT)


@respx.mock
def test_list(senders):
    respx.get(f"{BASE}{PATH}").mock(return_value=httpx.Response(200, json=LIST))
    res = senders.list()
    assert isinstance(res, SenderNumberList)
    assert res.sender_numbers[0].number == "07052361008"
    assert res.sender_numbers[0].verification_method == "own_provisioned"
    assert res.grace[0].number == "07052753809"
    assert res.limit is not None and res.limit.used == 1 and res.limit.limit == 5


@respx.mock
def test_list_limit_null_and_unknown_values(senders):
    respx.get(f"{BASE}{PATH}").mock(
        return_value=httpx.Response(
            200, json={"senderNumbers": [{**SAMPLE, "type": "future", "status": "future"}], "grace": [], "limit": None}
        )
    )
    res = senders.list()
    assert res.limit is None
    # 서버가 새 값을 보내도 파싱이 깨지지 않는다.
    assert res.sender_numbers[0].type == "future"


@respx.mock
def test_list_limit_undetermined(senders):
    respx.get(f"{BASE}{PATH}").mock(
        return_value=httpx.Response(
            200, json={"senderNumbers": [], "grace": [], "limit": {"used": 0, "limit": None, "reached": False}}
        )
    )
    res = senders.list()
    assert res.limit is not None and res.limit.limit is None


@respx.mock
def test_create(senders):
    route = respx.post(f"{BASE}{PATH}").mock(return_value=httpx.Response(201, json=SAMPLE))
    res = senders.create(number="070-5236-1008")
    assert isinstance(res, SenderNumber)
    assert res.id == "sn_1"
    assert json.loads(route.calls[0].request.content) == {"number": "070-5236-1008"}


@respx.mock
def test_create_conflict_code(senders):
    respx.post(f"{BASE}{PATH}").mock(
        return_value=httpx.Response(
            409, json={"error": "이미 등록된 발신번호입니다.", "code": "sender_number_already_registered"}
        )
    )
    with pytest.raises(ConflictError) as exc:
        senders.create(number="07052361008")
    assert exc.value.code == "sender_number_already_registered"


@respx.mock
def test_delete(senders):
    route = respx.delete(f"{BASE}{PATH}/sn_1").mock(return_value=httpx.Response(204))
    assert senders.delete("sn_1") is None
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_async_list_and_create():
    c = AsyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)
    try:
        s = AsyncSenderNumbers(client=c, account_id=ACCOUNT)
        respx.get(f"{BASE}{PATH}").mock(return_value=httpx.Response(200, json=LIST))
        respx.post(f"{BASE}{PATH}").mock(return_value=httpx.Response(201, json=SAMPLE))
        respx.delete(f"{BASE}{PATH}/sn_1").mock(return_value=httpx.Response(204))
        assert (await s.list()).sender_numbers[0].id == "sn_1"
        assert (await s.create(number="07052361008")).status == "approved"
        assert await s.delete("sn_1") is None
    finally:
        await c.close()


NUMBER = {"number": "07052753973", "source": "pool", "routingType": "webhook"}


class TestNumbersSmsSender:

    @respx.mock
    def test_registered_and_skipped(self, client):
        numbers = Numbers(client=client, account_id=ACCOUNT)
        respx.post(f"{BASE}/v1/accounts/{ACCOUNT}/numbers").mock(
            side_effect=[
                httpx.Response(201, json={**NUMBER, "smsSender": {"registered": True, "senderNumberId": "sn_9"}}),
                httpx.Response(
                    201,
                    json={**NUMBER, "smsSender": {"registered": False, "reason": "sender_number_limit_reached"}},
                ),
            ]
        )
        ok = numbers.create()
        assert isinstance(ok, ProvisionedPhoneNumber)
        assert ok.sms_sender is not None and ok.sms_sender.registered is True
        assert ok.sms_sender.sender_number_id == "sn_9"
        skipped = numbers.create()
        assert skipped.sms_sender is not None and skipped.sms_sender.registered is False
        assert skipped.sms_sender.reason == "sender_number_limit_reached"

    @respx.mock
    def test_old_server_without_sms_sender(self, client):
        numbers = Numbers(client=client, account_id=ACCOUNT)
        respx.post(f"{BASE}/v1/accounts/{ACCOUNT}/numbers").mock(return_value=httpx.Response(201, json=NUMBER))
        assert numbers.create().sms_sender is None
