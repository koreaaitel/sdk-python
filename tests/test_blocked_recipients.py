import json

import httpx
import pytest
import respx

from clawops._base_client import AsyncAPIClient, SyncAPIClient
from clawops.resources.blocked_recipients import AsyncBlockedRecipients, BlockedRecipients
from clawops.types.blocked_recipient import BlockedRecipient

BASE = "https://api.koreaaitel.com"
ACCOUNT = "AC1a2b3c4d"
PATH = f"/v1/accounts/{ACCOUNT}/blocked-recipients"

SAMPLE = {
    "id": "blk_1",
    "recipient": "01012345678",
    "channel": "call",
    "active": True,
    "source": "api",
    "sourceRef": None,
    "note": None,
    "createdBy": None,
    "createdAt": "2026-07-31T00:00:00Z",
    "updatedAt": "2026-07-31T00:00:00Z",
    "unblockedAt": None,
    "unblockedSource": None,
    "unblockedBy": None,
    "unblockedNote": None,
}


@pytest.fixture
def client():
    c = SyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)
    yield c
    c.close()


@pytest.fixture
def blocked(client):
    return BlockedRecipients(client=client, account_id=ACCOUNT)


class TestCreate:
    @respx.mock
    def test_create(self, blocked):
        route = respx.post(f"{BASE}{PATH}").mock(return_value=httpx.Response(201, json=SAMPLE))
        res = blocked.create(recipient="010-1234-5678", channel="call", note="상담 중 거부")

        assert isinstance(res, BlockedRecipient)
        # 서버가 정규화한 번호가 그대로 돌아온다.
        assert res.recipient == "01012345678"
        assert res.active is True
        assert res.channel == "call"

        body = json.loads(route.calls[0].request.content)
        assert body == {"recipient": "010-1234-5678", "channel": "call", "note": "상담 중 거부"}

    @respx.mock
    def test_create_omits_unset_options(self, blocked):
        route = respx.post(f"{BASE}{PATH}").mock(return_value=httpx.Response(201, json=SAMPLE))
        blocked.create(recipient="01012345678", channel="message")
        body = json.loads(route.calls[0].request.content)
        assert body == {"recipient": "01012345678", "channel": "message"}
        assert "source" not in body and "note" not in body

    @respx.mock
    def test_create_idempotent_200(self, blocked):
        """이미 차단 중이면 서버가 200 을 준다 — 에러가 아니다."""
        respx.post(f"{BASE}{PATH}").mock(return_value=httpx.Response(200, json=SAMPLE))
        res = blocked.create(recipient="01012345678", channel="call")
        assert res.id == "blk_1"

    @respx.mock
    def test_create_camel_case_source_ref(self, blocked):
        route = respx.post(f"{BASE}{PATH}").mock(return_value=httpx.Response(201, json=SAMPLE))
        blocked.create(recipient="01012345678", channel="call", source_ref="CA1", source="console")
        body = json.loads(route.calls[0].request.content)
        assert body["sourceRef"] == "CA1"
        assert body["source"] == "console"


class TestList:
    @respx.mock
    def test_list_with_filters(self, blocked):
        route = respx.get(f"{BASE}{PATH}").mock(
            return_value=httpx.Response(
                200, json={"data": [SAMPLE], "meta": {"page": 0, "pageSize": 20, "total": 1}}
            )
        )
        page = blocked.list(channel="call", status="active")

        assert len(page.data) == 1
        assert isinstance(page.data[0], BlockedRecipient)
        assert page.data[0].recipient == "01012345678"

        params = route.calls[0].request.url.params
        assert params["channel"] == "call"
        assert params["status"] == "active"

    @respx.mock
    def test_list_page_size_alias(self, blocked):
        route = respx.get(f"{BASE}{PATH}").mock(
            return_value=httpx.Response(
                200, json={"data": [], "meta": {"page": 0, "pageSize": 50, "total": 0}}
            )
        )
        blocked.list(page_size=50)
        assert route.calls[0].request.url.params["pageSize"] == "50"


class TestRetrieve:
    @respx.mock
    def test_retrieve(self, blocked):
        respx.get(f"{BASE}{PATH}/blk_1").mock(return_value=httpx.Response(200, json=SAMPLE))
        res = blocked.retrieve("blk_1")
        assert res.id == "blk_1"

    @respx.mock
    def test_retrieve_released_item(self, blocked):
        """해제된 항목도 이력으로 남아 조회된다."""
        released = {**SAMPLE, "active": False, "unblockedAt": "2026-07-31T01:00:00Z",
                    "unblockedSource": "api"}
        respx.get(f"{BASE}{PATH}/blk_1").mock(return_value=httpx.Response(200, json=released))
        res = blocked.retrieve("blk_1")
        assert res.active is False
        assert res.unblocked_at is not None
        assert res.unblocked_source == "api"


class TestUpdate:
    @respx.mock
    def test_update_note(self, blocked):
        route = respx.patch(f"{BASE}{PATH}/blk_1").mock(
            return_value=httpx.Response(200, json={**SAMPLE, "note": "2차 확인"})
        )
        res = blocked.update("blk_1", note="2차 확인")
        assert res.note == "2차 확인"
        assert json.loads(route.calls[0].request.content) == {"note": "2차 확인"}

    @respx.mock
    def test_update_clears_note_with_none(self, blocked):
        route = respx.patch(f"{BASE}{PATH}/blk_1").mock(
            return_value=httpx.Response(200, json=SAMPLE)
        )
        blocked.update("blk_1")
        assert json.loads(route.calls[0].request.content) == {"note": None}


class TestRelease:
    @respx.mock
    def test_release_returns_item_not_none(self, blocked):
        """DELETE 지만 삭제가 아니라 해제된 항목을 돌려준다 — 이 endpoint 의 핵심 계약."""
        released = {
            **SAMPLE,
            "active": False,
            "unblockedAt": "2026-07-31T01:00:00Z",
            "unblockedSource": "api",
            "unblockedNote": "고객 재동의",
        }
        route = respx.delete(f"{BASE}{PATH}/blk_1").mock(
            return_value=httpx.Response(200, json=released)
        )
        res = blocked.release("blk_1", note="고객 재동의")

        assert isinstance(res, BlockedRecipient)
        assert res.active is False
        assert res.unblocked_note == "고객 재동의"
        assert json.loads(route.calls[0].request.content) == {"note": "고객 재동의"}

    @respx.mock
    def test_release_without_note_sends_no_body(self, blocked):
        route = respx.delete(f"{BASE}{PATH}/blk_1").mock(
            return_value=httpx.Response(200, json={**SAMPLE, "active": False})
        )
        blocked.release("blk_1")
        assert route.calls[0].request.content in (b"", None)


class TestAsync:
    @pytest.mark.asyncio
    @respx.mock
    async def test_async_create_and_release(self):
        client = AsyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)
        blocked = AsyncBlockedRecipients(client=client, account_id=ACCOUNT)
        try:
            respx.post(f"{BASE}{PATH}").mock(return_value=httpx.Response(201, json=SAMPLE))
            created = await blocked.create(recipient="01012345678", channel="call")
            assert created.id == "blk_1"

            respx.delete(f"{BASE}{PATH}/blk_1").mock(
                return_value=httpx.Response(
                    200, json={**SAMPLE, "active": False, "unblockedAt": "2026-07-31T01:00:00Z"}
                )
            )
            released = await blocked.release("blk_1")
            assert released.active is False
        finally:
            await client.close()


# ⛔ 이 클래스가 지키는 것은 **채널이 늘어도 SDK 가 안 죽는 것**이다.
#
#    channel 을 Literal["call", "message"] 로 굳혀 뒀다가 서버가 email 을 더한 순간,
#    이메일 항목이 하나라도 섞인 목록 조회가 **파싱 단계에서 통째로 실패**했다. 고객은
#    자기가 콘솔에서 등록한 항목 때문에 SDK 가 깨지는 것을 겪는다. 응답 타입을 좁히는
#    것은 서버가 값을 늘리는 날 옛 SDK 를 전부 깨뜨리는 일이다.
class TestChannelExtensibility:
    @respx.mock
    def test_email_channel_parses(self, blocked):
        respx.get(f"{BASE}{PATH}/blk_1").mock(
            return_value=httpx.Response(
                200, json={**SAMPLE, "recipient": "kim@example.com", "channel": "email"}
            )
        )
        res = blocked.retrieve("blk_1")
        assert res.channel == "email"
        assert res.recipient == "kim@example.com"

    @respx.mock
    def test_create_email(self, blocked):
        route = respx.post(f"{BASE}{PATH}").mock(
            return_value=httpx.Response(
                201, json={**SAMPLE, "recipient": "kim@example.com", "channel": "email"}
            )
        )
        blocked.create(recipient="Kim@Example.com", channel="email")
        # 소문자 정규화는 **서버가** 한다 — SDK 가 흉내내면 규칙이 두 곳이 되고 갈린다.
        body = json.loads(route.calls[0].request.content)
        assert body == {"recipient": "Kim@Example.com", "channel": "email"}

    @respx.mock
    def test_unknown_channel_does_not_break(self, blocked):
        respx.get(f"{BASE}{PATH}/blk_1").mock(
            return_value=httpx.Response(200, json={**SAMPLE, "channel": "push"})
        )
        assert blocked.retrieve("blk_1").channel == "push"

    @respx.mock
    def test_internal_sources_parse(self, blocked):
        # ARS 9번·문자 회신으로 들어온 항목은 source 가 'ars'·'sms' 다. 공개 API 로 등록할
        # 수 있는 값만 허용하면 그 항목들에서 파싱이 깨진다.
        respx.get(f"{BASE}{PATH}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {**SAMPLE, "source": "ars"},
                        {**SAMPLE, "id": "blk_2", "source": "sms"},
                    ],
                    "meta": {"page": 0, "pageSize": 20, "total": 2},
                },
            )
        )
        page = blocked.list()
        assert [r.source for r in page.data] == ["ars", "sms"]
