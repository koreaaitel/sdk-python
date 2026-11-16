import json

import httpx
import pytest
import respx

from clawops._base_client import AsyncAPIClient, SyncAPIClient
from clawops.resources.kakao import AsyncKakao, Kakao
from clawops.types.kakao import (
    KakaoBrandTemplate,
    KakaoChannel,
    KakaoChannelCategoryList,
    KakaoTemplate,
    KakaoTokenRequest,
)

BASE = "https://api.koreaaitel.com"
ACCOUNT = "AC1a2b3c4d"
CHANNELS = f"/v1/accounts/{ACCOUNT}/kakao/channels"
TEMPLATES = f"/v1/accounts/{ACCOUNT}/kakao/templates"
BRAND_TEMPLATES = f"/v1/accounts/{ACCOUNT}/kakao/brand-templates"
CATEGORIES = f"/v1/accounts/{ACCOUNT}/kakao/channel-categories"

# app/spec 의 example 을 그대로 옮긴 것. 픽스처가 상상한 페이로드면 계약을 못 지킨다.
CHANNEL_JSON = {
    "id": "clx9kak0001",
    "searchId": "example",
    "name": "러너스 고객센터",
    "categoryCode": "00100010001",
    "status": "connected",
    "managerPhoneMasked": "010-****-5678",
    "connectedAt": "2026-08-01T00:00:00Z",
    "syncedAt": None,
    "createdAt": "2026-08-01T00:00:00Z",
    "updatedAt": "2026-08-01T00:00:00Z",
}
TEMPLATE_JSON = {
    "id": "clx9tpl0001",
    "channelId": "clx9kak0001",
    "name": "주문 접수 안내",
    "content": "#{고객명}님, 주문이 접수되었습니다.",
    "status": "APPROVED",
    "dormant": False,
    "sendable": True,
    "assignType": "CHANNEL",
    "messageType": "BA",
    "emphasizeType": "NONE",
    "variables": ["#{고객명}"],
    "createdAt": "2026-08-01T00:00:00Z",
    "updatedAt": "2026-08-01T00:00:00Z",
}


def _page(items, *, total=None, page=0, page_size=20):
    return {"data": items, "meta": {"total": total if total is not None else len(items),
                                    "page": page, "pageSize": page_size}}


@pytest.fixture
def client():
    c = SyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)
    yield c
    c.close()


@pytest.fixture
def kakao(client):
    return Kakao(client=client, account_id=ACCOUNT)


@pytest.fixture
def async_client():
    yield AsyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)


@pytest.fixture
def async_kakao(async_client):
    return AsyncKakao(client=async_client, account_id=ACCOUNT)


class TestChannelsList:
    @respx.mock
    def test_list(self, kakao):
        respx.get(f"{BASE}{CHANNELS}").mock(return_value=httpx.Response(200, json=_page([CHANNEL_JSON])))
        items = list(kakao.channels.list())
        assert len(items) == 1
        assert isinstance(items[0], KakaoChannel)
        assert items[0].id == "clx9kak0001"
        assert items[0].search_id == "example"
        assert items[0].manager_phone_masked == "010-****-5678"
        assert items[0].synced_at is None

    @respx.mock
    def test_list_filters(self, kakao):
        route = respx.get(f"{BASE}{CHANNELS}").mock(return_value=httpx.Response(200, json=_page([])))
        kakao.channels.list(status="connected", page=1, page_size=50)
        url = str(route.calls[0].request.url)
        assert "status=connected" in url
        assert "page=1" in url
        assert "pageSize=50" in url

    @respx.mock
    def test_auto_paging(self, kakao):
        respx.get(f"{BASE}{CHANNELS}").mock(side_effect=[
            httpx.Response(200, json=_page([CHANNEL_JSON], total=2, page=0, page_size=1)),
            httpx.Response(200, json=_page([{**CHANNEL_JSON, "id": "clx9kak0002"}],
                                           total=2, page=1, page_size=1)),
        ])
        ids = [c.id for c in kakao.channels.list().auto_paging_iter()]
        assert ids == ["clx9kak0001", "clx9kak0002"]

    @respx.mock
    def test_unknown_status_does_not_raise(self, kakao):
        """서버가 상태를 늘려도 목록 전체가 죽지 않는다."""
        respx.get(f"{BASE}{CHANNELS}").mock(
            return_value=httpx.Response(200, json=_page([{**CHANNEL_JSON, "status": "suspended"}]))
        )
        assert [c.status for c in kakao.channels.list()] == ["suspended"]


class TestChannelsRetrieve:
    @respx.mock
    def test_retrieve(self, kakao):
        route = respx.get(f"{BASE}{CHANNELS}/clx9kak0001").mock(
            return_value=httpx.Response(200, json=CHANNEL_JSON)
        )
        ch = kakao.channels.retrieve("clx9kak0001")
        assert route.called
        assert ch.id == "clx9kak0001"

    @respx.mock
    def test_needs_attention_is_not_an_error(self, kakao):
        """카카오 조회 실패는 404 가 아니라 needs_attention 이다."""
        respx.get(f"{BASE}{CHANNELS}/clx9kak0001").mock(
            return_value=httpx.Response(200, json={**CHANNEL_JSON, "status": "needs_attention"})
        )
        assert kakao.channels.retrieve("clx9kak0001").status == "needs_attention"


class TestChannelsConnect:
    @respx.mock
    def test_request_token(self, kakao):
        route = respx.post(f"{BASE}{CHANNELS}/token").mock(return_value=httpx.Response(202, json={
            "requested": True, "searchId": "example",
            "phoneNumberMasked": "010-****-5678", "retryAfterSeconds": 60,
        }))
        res = kakao.channels.request_token(search_id="@example", phone_number="010-1234-5678")
        assert json.loads(route.calls[0].request.content) == {
            "searchId": "@example", "phoneNumber": "010-1234-5678",
        }
        assert isinstance(res, KakaoTokenRequest)
        assert res.requested is True
        assert res.retry_after_seconds == 60
        # 인증번호 자체는 응답에 없다 — 담당자 휴대전화로만 간다.
        assert not hasattr(res, "token")

    @respx.mock
    def test_connect(self, kakao):
        route = respx.post(f"{BASE}{CHANNELS}").mock(return_value=httpx.Response(201, json=CHANNEL_JSON))
        ch = kakao.channels.connect(
            search_id="example", phone_number="010-1234-5678",
            category_code="00100010001", token="394812",
        )
        assert json.loads(route.calls[0].request.content) == {
            "searchId": "example", "phoneNumber": "010-1234-5678",
            "categoryCode": "00100010001", "token": "394812",
        }
        assert ch.id == "clx9kak0001"

    @respx.mock
    def test_connect_idempotent_200(self, kakao):
        """이미 연결된 채널이면 인증번호를 소모하지 않고 200 으로 기존 연결을 준다."""
        respx.post(f"{BASE}{CHANNELS}").mock(return_value=httpx.Response(200, json=CHANNEL_JSON))
        assert kakao.channels.connect(
            search_id="example", phone_number="010", category_code="001", token="1"
        ).id == "clx9kak0001"


class TestChannelsDisconnect:
    @respx.mock
    def test_disconnect_returns_channel(self, kakao):
        route = respx.delete(f"{BASE}{CHANNELS}/clx9kak0001").mock(
            return_value=httpx.Response(200, json=CHANNEL_JSON)
        )
        ch = kakao.channels.disconnect("clx9kak0001")
        assert route.called
        assert ch.id == "clx9kak0001"


class TestTemplates:
    @respx.mock
    def test_list_requires_channel_id_in_query(self, kakao):
        route = respx.get(f"{BASE}{TEMPLATES}").mock(
            return_value=httpx.Response(200, json=_page([TEMPLATE_JSON]))
        )
        items = list(kakao.templates.list(channel_id="clx9kak0001"))
        assert "channelId=clx9kak0001" in str(route.calls[0].request.url)
        assert isinstance(items[0], KakaoTemplate)
        assert items[0].channel_id == "clx9kak0001"
        assert items[0].sendable is True
        assert items[0].variables == ["#{고객명}"]

    @respx.mock
    def test_list_paging(self, kakao):
        route = respx.get(f"{BASE}{TEMPLATES}").mock(return_value=httpx.Response(200, json=_page([])))
        kakao.templates.list(channel_id="c1", page=2, page_size=100)
        url = str(route.calls[0].request.url)
        assert "page=2" in url and "pageSize=100" in url

    @respx.mock
    def test_open_vocabulary_status(self, kakao):
        """카카오 검수 상태는 열린 집합이라 모르는 값도 통과해야 한다."""
        respx.get(f"{BASE}{TEMPLATES}").mock(return_value=httpx.Response(
            200, json=_page([{**TEMPLATE_JSON, "status": "REVIEWING", "sendable": False}])
        ))
        t = list(kakao.templates.list(channel_id="c1"))[0]
        assert t.status == "REVIEWING"
        assert t.sendable is False


BRAND_TEMPLATE_JSON = {
    "id": "clx9bms0001",
    "channelId": "clx9kak0001",
    "name": "9월 신상품 안내",
    "chatBubbleType": "TEXT",
    "content": "#{고객명}님, 9월 신상품이 도착했습니다.",
    "header": None,
    "variables": ["#{고객명}"],
    "createdAt": "2026-09-01T00:00:00Z",
    "updatedAt": "2026-09-01T00:00:00Z",
}


class TestBrandTemplates:
    @respx.mock
    def test_uses_its_own_path(self, kakao):
        """알림톡 템플릿과 **다른 표**다 — 경로가 갈린다."""
        route = respx.get(f"{BASE}{BRAND_TEMPLATES}").mock(
            return_value=httpx.Response(200, json=_page([BRAND_TEMPLATE_JSON]))
        )
        items = list(kakao.brand_templates.list(channel_id="clx9kak0001"))
        url = str(route.calls[0].request.url)
        assert "/kakao/brand-templates" in url
        assert "channelId=clx9kak0001" in url
        assert isinstance(items[0], KakaoBrandTemplate)
        assert items[0].id == "clx9bms0001"

    @respx.mock
    def test_content_and_header_are_nullable(self, kakao):
        """본문이 담기는 자리가 버블 타입마다 다르다 — TEXT·IMAGE·WIDE 만 content 가 찬다."""
        respx.get(f"{BASE}{BRAND_TEMPLATES}").mock(return_value=httpx.Response(
            200,
            json=_page([{
                **BRAND_TEMPLATE_JSON,
                "chatBubbleType": "WIDE_ITEM_LIST",
                "content": None,
                "header": "가을 기획전",
            }]),
        ))
        t = list(kakao.brand_templates.list(channel_id="c1"))[0]
        assert t.content is None
        assert t.header == "가을 기획전"

    @respx.mock
    def test_open_vocabulary_bubble_type(self, kakao):
        """카카오가 유형을 늘리면 닫힌 Literal 은 목록 전체를 던진다."""
        respx.get(f"{BASE}{BRAND_TEMPLATES}").mock(return_value=httpx.Response(
            200, json=_page([{**BRAND_TEMPLATE_JSON, "chatBubbleType": "아직-없는-유형"}])
        ))
        t = list(kakao.brand_templates.list(channel_id="c1"))[0]
        assert t.chat_bubble_type == "아직-없는-유형"


class TestChannelCategories:
    @respx.mock
    def test_channel_categories(self, kakao):
        route = respx.get(f"{BASE}{CATEGORIES}").mock(return_value=httpx.Response(200, json={
            "data": [{"code": "00100010001", "name": "고객센터"}],
            "meta": {"fetchedAt": "2026-08-01T00:00:00Z", "cached": True},
        }))
        res = kakao.channel_categories()
        assert route.called
        assert isinstance(res, KakaoChannelCategoryList)
        assert res.data[0].code == "00100010001"
        assert res.meta.cached is True


class TestAccountScoping:
    @respx.mock
    def test_other_account_path(self, client):
        from clawops.resources.accounts import AccountContext

        other = AccountContext(client=client, account_id="AC_other")
        route = respx.get(f"{BASE}/v1/accounts/AC_other/kakao/channels").mock(
            return_value=httpx.Response(200, json=_page([]))
        )
        other.kakao.channels.list()
        assert route.called


class TestAsyncKakao:
    @respx.mock
    @pytest.mark.asyncio
    async def test_list_channels(self, async_kakao):
        respx.get(f"{BASE}{CHANNELS}").mock(return_value=httpx.Response(200, json=_page([CHANNEL_JSON])))
        page = await async_kakao.channels.list()
        assert [c.id for c in page] == ["clx9kak0001"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_request_token(self, async_kakao):
        route = respx.post(f"{BASE}{CHANNELS}/token").mock(return_value=httpx.Response(202, json={
            "requested": True, "searchId": "example",
            "phoneNumberMasked": "010-****-5678", "retryAfterSeconds": 60,
        }))
        res = await async_kakao.channels.request_token(search_id="example", phone_number="010-1234-5678")
        assert route.called
        assert res.search_id == "example"

    @respx.mock
    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self, async_kakao):
        respx.post(f"{BASE}{CHANNELS}").mock(return_value=httpx.Response(201, json=CHANNEL_JSON))
        respx.delete(f"{BASE}{CHANNELS}/clx9kak0001").mock(return_value=httpx.Response(200, json=CHANNEL_JSON))
        ch = await async_kakao.channels.connect(
            search_id="example", phone_number="010", category_code="001", token="1"
        )
        assert ch.id == "clx9kak0001"
        assert (await async_kakao.channels.disconnect("clx9kak0001")).id == "clx9kak0001"

    @respx.mock
    @pytest.mark.asyncio
    async def test_brand_templates(self, async_kakao):
        """AsyncKakaoBrandTemplates 는 손으로 쓴 두 번째 벌이라 여기서만 실행된다."""
        route = respx.get(f"{BASE}{BRAND_TEMPLATES}").mock(
            return_value=httpx.Response(200, json=_page([BRAND_TEMPLATE_JSON]))
        )
        page = await async_kakao.brand_templates.list(channel_id="clx9kak0001")
        url = str(route.calls[0].request.url)
        assert "/kakao/brand-templates" in url
        assert "channelId=clx9kak0001" in url
        assert [t.id for t in page] == ["clx9bms0001"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_templates_and_categories(self, async_kakao):
        route = respx.get(f"{BASE}{TEMPLATES}").mock(
            return_value=httpx.Response(200, json=_page([TEMPLATE_JSON]))
        )
        page = await async_kakao.templates.list(channel_id="clx9kak0001")
        assert "channelId=clx9kak0001" in str(route.calls[0].request.url)
        assert [t.id for t in page] == ["clx9tpl0001"]

        respx.get(f"{BASE}{CATEGORIES}").mock(return_value=httpx.Response(200, json={
            "data": [{"code": "001", "name": "고객센터"}],
            "meta": {"fetchedAt": "2026-08-01T00:00:00Z", "cached": False},
        }))
        cats = await async_kakao.channel_categories()
        assert cats.data[0].name == "고객센터"


class TestBrandImages:
    """자유형에 실을 이미지 — 업로드는 multipart 다."""

    @respx.mock
    def test_upload_sends_multipart(self, kakao):
        route = respx.post(f"{BASE}/v1/accounts/{ACCOUNT}/kakao/brand-images").mock(
            return_value=httpx.Response(201, json={"id": "cmtn8o4vb000a"})
        )

        result = kakao.brand_images.upload(
            file=b"\x89PNG", filename="banner.png", bubble_type="WIDE"
        )
        assert result.id == "cmtn8o4vb000a"

        request = route.calls[0].request
        # ⛔ boundary 는 httpx 가 붙인다. 우리가 application/json 을 남겨 두면 서버가 본문을
        #    파싱하지 못하고, 그 실패는 "왜 파일이 안 왔지" 로만 드러난다.
        assert request.headers["content-type"].startswith("multipart/form-data")

        body = request.content.decode("utf-8", errors="replace")
        assert 'name="bubbleType"' in body
        assert "WIDE" in body
        assert 'filename="banner.png"' in body
        # slot 을 안 주면 필드 자체가 없다 — 서버 기본값(main)에 맡긴다.
        assert 'name="slot"' not in body

    @respx.mock
    def test_upload_carries_slot(self, kakao):
        route = respx.post(f"{BASE}/v1/accounts/{ACCOUNT}/kakao/brand-images").mock(
            return_value=httpx.Response(201, json={"id": "cmtn8o54u000b"})
        )
        kakao.brand_images.upload(
            file=b"x", filename="sub.png", bubble_type="WIDE_ITEM_LIST", slot="sub"
        )
        body = route.calls[0].request.content.decode("utf-8", errors="replace")
        # ⚠️ 값까지 본다 — `"sub" in body` 는 파일 이름(sub.png)만으로도 참이라 공허하다.
        assert 'name="slot"\r\n\r\nsub' in body

    @respx.mock
    def test_list_parses(self, kakao):
        respx.get(f"{BASE}/v1/accounts/{ACCOUNT}/kakao/brand-images").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "cmtn8o4vb000a",
                            "bubbleType": "WIDE",
                            "slot": "main",
                            "name": "banner.png",
                            "createdAt": "2026-09-06T09:07:07.150Z",
                        }
                    ],
                    "meta": {"page": 0, "pageSize": 20, "total": 1},
                },
            )
        )
        page = kakao.brand_images.list()
        assert page.data[0].id == "cmtn8o4vb000a"
        assert page.data[0].bubble_type == "WIDE"
        assert page.data[0].name == "banner.png"
