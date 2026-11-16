import json
import httpx
import pytest
import respx

from clawops._base_client import SyncAPIClient
from clawops.resources.messages import Messages
from clawops.types.message import Message

BASE = "https://api.koreaaitel.com"
ACCOUNT = "AC1a2b3c4d"
MESSAGES_PATH = f"/v1/accounts/{ACCOUNT}/messages"

MESSAGE_JSON = {
    "messageId": "MG0123456789abcdef0123456789abcdef",
    "status": "queued", "type": "sms",
    "to": "01012345678", "from": "07052358010",
    "body": "안녕하세요", "direction": "outbound",
    "accountId": "AC1a2b3c4d",
    "dateCreated": "2025-06-01T12:00:00Z", "dateUpdated": None,
}

# 알림톡. 서버가 실제로 주는 모양이다 — type 은 'kakao' 가 아니라 'ata' 이고
# body 에는 템플릿 변수를 치환한 결과가 담긴다.
ATA_JSON = {
    **MESSAGE_JSON,
    "messageId": "MGata0123456789abcdef0123456789ab",
    "type": "ata",
    "status": "sent",
    "body": "홍길동님, 주문이 접수되었습니다.",
}

BMS_JSON = {
    **ATA_JSON,
    "messageId": "MG_bms",
    "type": "bms",
    "body": "홍길동님, 9월 신상품이 도착했습니다.",
}


@pytest.fixture
def client():
    c = SyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)
    yield c
    c.close()


@pytest.fixture
def messages(client):
    return Messages(client=client, account_id=ACCOUNT)


class TestMessagesCreate:
    @respx.mock
    def test_create_sms(self, messages):
        respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=MESSAGE_JSON))
        msg = messages.create(to="01012345678", from_="07052358010", body="안녕하세요")
        assert isinstance(msg, Message)
        assert msg.message_id == "MG0123456789abcdef0123456789abcdef"
        assert msg.from_ == "07052358010"
        assert msg.type == "sms"

    @respx.mock
    def test_create_mms(self, messages):
        mms_json = {**MESSAGE_JSON, "type": "mms"}
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=mms_json))
        messages.create(to="010", from_="070", body="사진", type="mms", subject="제목")
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Type"] == "mms"
        assert parsed["Subject"] == "제목"

    @respx.mock
    def test_create_mms_with_media(self, messages):
        mms_json = {
            **MESSAGE_JSON,
            "type": "mms",
            "numMedia": 1,
            "mediaUrl": ["https://example.com/image.jpg"],
        }
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(
            return_value=httpx.Response(201, json=mms_json)
        )
        msg = messages.create(
            to="010", from_="070", body="사진",
            type="mms", subject="제목",
            media_url=["https://example.com/image.jpg"],
        )
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Type"] == "mms"
        assert parsed["MediaUrl"] == ["https://example.com/image.jpg"]
        assert msg.num_media == 1
        assert msg.media_url == ["https://example.com/image.jpg"]


class TestMessagesCreateKakao:
    """알림톡 발송 — 중첩 Kakao/Fallback 조립과 배타 규칙."""

    @respx.mock
    def test_create_ata(self, messages):
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=ATA_JSON))
        msg = messages.create(
            to="01012345678", from_="07052358010",
            kakao={
                "channel_id": "clx9kak0001",
                "template_id": "clx9tpl0001",
                "variables": {"고객명": "홍길동"},
            },
        )
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Kakao"] == {
            "ChannelId": "clx9kak0001",
            "TemplateId": "clx9tpl0001",
            "Variables": {"고객명": "홍길동"},
        }
        # 알림톡에는 Body/Type 을 얹지 않는다 — 서버가 Kakao 로 판별한다.
        assert "Body" not in parsed
        assert "Type" not in parsed
        assert "Fallback" not in parsed
        assert msg.type == "ata"

    @respx.mock
    def test_create_ata_without_variables(self, messages):
        """변수 없는 템플릿이면 Variables 키 자체가 없다 (strip_not_given 은 얕다)."""
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=ATA_JSON))
        messages.create(to="010", from_="070", kakao={"channel_id": "c1", "template_id": "t1"})
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Kakao"] == {"ChannelId": "c1", "TemplateId": "t1"}

    @respx.mock
    def test_create_bms(self, messages):
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=BMS_JSON))
        msg = messages.create(
            to="01012345678", from_="07052358010",
            brand={
                "channel_id": "clx9kak0001",
                "template_id": "clx9bms0001",
                "variables": {"고객명": "홍길동"},
            },
        )
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Brand"] == {
            "ChannelId": "clx9kak0001",
            "TemplateId": "clx9bms0001",
            "Variables": {"고객명": "홍길동"},
        }
        # 본문은 템플릿이 정한다. 알림톡 칸과 섞이지도 않아야 한다.
        assert "Body" not in parsed
        assert "Type" not in parsed
        assert "Kakao" not in parsed
        assert msg.type == "bms"

    @respx.mock
    def test_create_ata_with_fallback(self, messages):
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=ATA_JSON))
        messages.create(
            to="010", from_="070",
            kakao={"channel_id": "c1", "template_id": "t1"},
            fallback={"body": "주문이 접수되었습니다.", "type": "lms", "subject": "알림"},
        )
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Fallback"] == {"Type": "lms", "Subject": "알림", "Body": "주문이 접수되었습니다."}

    @respx.mock
    def test_create_ata_with_fallback_disabled(self, messages):
        """Disabled=False 는 의미가 있으므로 살아 있어야 한다 (None 만 제거된다)."""
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=ATA_JSON))
        messages.create(
            to="010", from_="070",
            kakao={"channel_id": "c1", "template_id": "t1"},
            fallback={"disabled": True},
        )
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Fallback"] == {"Disabled": True}

    @respx.mock
    def test_create_ata_accepts_explicit_type(self, messages):
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=ATA_JSON))
        messages.create(to="010", from_="070", kakao={"channel_id": "c1", "template_id": "t1"}, type="ata")
        assert json.loads(route.calls[0].request.content)["Type"] == "ata"


class TestMessagesCreateExclusivity:
    """타입체커를 돌리지 않는 사용자에게도 400 대신 무엇이 잘못됐는지 알려준다.

    같은 규칙을 tests/typing 이 컴파일 시점에도 고정한다.
    """

    KAKAO = {"channel_id": "c1", "template_id": "t1"}

    def test_body_with_kakao_rejected(self, messages):
        with pytest.raises(TypeError, match="함께 보낼 수 없는"):
            messages.create(to="010", from_="070", body="안녕", kakao=self.KAKAO)

    def test_media_url_with_kakao_rejected(self, messages):
        with pytest.raises(TypeError, match="media_url"):
            messages.create(to="010", from_="070", kakao=self.KAKAO, media_url=["https://e.com/a.jpg"])

    def test_subject_with_kakao_rejected(self, messages):
        with pytest.raises(TypeError, match="subject"):
            messages.create(to="010", from_="070", kakao=self.KAKAO, subject="제목")

    def test_conflicting_type_rejected(self, messages):
        with pytest.raises(TypeError, match="'ata'"):
            messages.create(to="010", from_="070", kakao=self.KAKAO, type="sms")

    def test_fallback_without_kakao_rejected(self, messages):
        with pytest.raises(TypeError, match="알림톡 전용"):
            messages.create(to="010", from_="070", body="안녕", fallback={"body": "x"})

    def test_neither_body_nor_kakao_rejected(self, messages):
        with pytest.raises(TypeError, match="반드시"):
            messages.create(to="010", from_="070")

    BRAND = {"channel_id": "c1", "template_id": "t1"}

    def test_body_with_brand_rejected(self, messages):
        with pytest.raises(TypeError, match="함께 보낼 수 없는"):
            messages.create(to="010", from_="070", body="안녕", brand=self.BRAND)

    def test_conflicting_type_with_brand_rejected(self, messages):
        with pytest.raises(TypeError, match="'bms'"):
            messages.create(to="010", from_="070", brand=self.BRAND, type="sms")

    def test_fallback_with_brand_rejected(self, messages):
        with pytest.raises(TypeError, match="대체발송이 없습니다"):
            messages.create(to="010", from_="070", brand=self.BRAND, fallback={"body": "x"})

    def test_kakao_with_brand_rejected(self, messages):
        """둘을 같이 실으면 어느 쪽으로 나갈지 정해 줄 수 없다 — 서버는 kakao_type_conflict."""
        with pytest.raises(TypeError, match="함께 보낼 수 없습니다"):
            messages.create(to="010", from_="070", kakao=self.KAKAO, brand=self.BRAND)

    def test_nothing_is_sent_when_rejected(self, messages):
        """거절은 HTTP 이전이다 — respx 를 걸지 않아도 네트워크가 나가지 않는다."""
        with pytest.raises(TypeError):
            messages.create(to="010", from_="070", body="안녕", kakao=self.KAKAO)


class TestMessagesList:
    @respx.mock
    def test_list_messages(self, messages):
        respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [MESSAGE_JSON], "meta": {"total": 1, "page": 0, "pageSize": 20},
        }))
        page = messages.list()
        items = list(page)
        assert len(items) == 1
        assert isinstance(items[0], Message)

    @respx.mock
    def test_list_with_filters(self, messages):
        route = respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [], "meta": {"total": 0, "page": 0, "pageSize": 10},
        }))
        messages.list(type="sms", status="sent", page=0, page_size=10)
        url = str(route.calls[0].request.url)
        assert "type=sms" in url
        assert "status=sent" in url
        assert "pageSize=10" in url

    @respx.mock
    def test_list_ata_and_number_filters(self, messages):
        """알림톡만 골라 보기 + 번호 필터. 둘 다 서버엔 있는데 SDK 에 없던 필터다."""
        route = respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [], "meta": {"total": 0, "page": 0, "pageSize": 20},
        }))
        messages.list(type="ata", number="07052358010")
        url = str(route.calls[0].request.url)
        assert "type=ata" in url
        assert "number=07052358010" in url


class TestMessagesGet:
    @respx.mock
    def test_get_message(self, messages):
        mid = "MG0123456789abcdef0123456789abcdef"
        respx.get(f"{BASE}{MESSAGES_PATH}/{mid}").mock(return_value=httpx.Response(200, json=MESSAGE_JSON))
        msg = messages.get(mid)
        assert msg.message_id == mid
        assert msg.body == "안녕하세요"


class TestAtaResponseParsing:
    """회귀: 알림톡 응답이 SDK 를 던지게 하던 것.

    서버는 알림톡을 `type: "ata"` 로 주는데 모델이 `"kakao"` 를 기다리고 있었다.
    `"kakao"` 는 서버가 한 번도 보낸 적 없는 값이다. 그래서 콘솔로 알림톡을 한 번이라도
    보낸 계정은 문자 조회까지 통째로 막혔다 — 목록은 페이지에 알림톡 한 건만 섞여도
    전부 실패했다.
    """

    @respx.mock
    def test_get_ata_does_not_raise(self, messages):
        mid = ATA_JSON["messageId"]
        respx.get(f"{BASE}{MESSAGES_PATH}/{mid}").mock(return_value=httpx.Response(200, json=ATA_JSON))
        msg = messages.get(mid)
        assert msg.type == "ata"
        assert msg.body == "홍길동님, 주문이 접수되었습니다."

    @respx.mock
    def test_list_with_ata_mixed_in_does_not_raise(self, messages):
        """알림톡 한 건이 섞였다고 목록 전체가 죽지 않는다.

        이 경로는 특히 나빴다. 아이템 검증이 클라이언트의 try 밖(resources/messages.py)
        에서 돌아 raw pydantic.ValidationError 가 그대로 샜다 — ClawOpsError 를 상속하지
        않으므로 사용자의 except ClawOpsError 에도 걸리지 않았다.
        """
        respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [MESSAGE_JSON, ATA_JSON], "meta": {"total": 2, "page": 0, "pageSize": 20},
        }))
        types = [m.type for m in messages.list()]
        assert types == ["sms", "ata"]

    @respx.mock
    def test_unknown_type_does_not_raise(self, messages):
        """서버가 유형을 새로 늘려도 던지지 않는다 — 같은 사고를 두 번 겪지 않기 위해."""
        mid = "MGfuture00000000000000000000000000"
        respx.get(f"{BASE}{MESSAGES_PATH}/{mid}").mock(
            return_value=httpx.Response(200, json={**MESSAGE_JSON, "messageId": mid, "type": "rcs"})
        )
        assert messages.get(mid).type == "rcs"

    @respx.mock
    def test_unknown_status_does_not_raise(self, messages):
        mid = "MGfuture00000000000000000000000001"
        respx.get(f"{BASE}{MESSAGES_PATH}/{mid}").mock(
            return_value=httpx.Response(200, json={**MESSAGE_JSON, "messageId": mid, "status": "pending_review"})
        )
        assert messages.get(mid).status == "pending_review"


# --- Async Tests ---

from clawops._base_client import AsyncAPIClient
from clawops.resources.messages import AsyncMessages


@pytest.fixture
def async_client():
    c = AsyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)
    yield c


@pytest.fixture
def async_messages(async_client):
    return AsyncMessages(client=async_client, account_id=ACCOUNT)


class TestAsyncMessagesCreate:
    @respx.mock
    @pytest.mark.asyncio
    async def test_create_sms(self, async_messages):
        respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=MESSAGE_JSON))
        msg = await async_messages.create(to="01012345678", from_="07052358010", body="안녕하세요")
        assert isinstance(msg, Message)
        assert msg.message_id == "MG0123456789abcdef0123456789abcdef"

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_mms(self, async_messages):
        mms_json = {**MESSAGE_JSON, "type": "mms"}
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=mms_json))
        await async_messages.create(to="010", from_="070", body="사진", type="mms", subject="제목")
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Type"] == "mms"


class TestAsyncMessagesList:
    @respx.mock
    @pytest.mark.asyncio
    async def test_list_messages(self, async_messages):
        respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [MESSAGE_JSON], "meta": {"total": 1, "page": 0, "pageSize": 20},
        }))
        page = await async_messages.list()
        items = list(page)
        assert len(items) == 1
        assert isinstance(items[0], Message)

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_with_filters(self, async_messages):
        route = respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [], "meta": {"total": 0, "page": 0, "pageSize": 10},
        }))
        await async_messages.list(type="sms", status="sent", page=0, page_size=10)
        url = str(route.calls[0].request.url)
        assert "type=sms" in url
        assert "status=sent" in url


class TestAsyncMessagesGet:
    @respx.mock
    @pytest.mark.asyncio
    async def test_get_message(self, async_messages):
        mid = "MG0123456789abcdef0123456789abcdef"
        respx.get(f"{BASE}{MESSAGES_PATH}/{mid}").mock(return_value=httpx.Response(200, json=MESSAGE_JSON))
        msg = await async_messages.get(mid)
        assert msg.message_id == mid


class TestAsyncAtaResponseParsing:
    @respx.mock
    @pytest.mark.asyncio
    async def test_get_ata_does_not_raise(self, async_messages):
        mid = ATA_JSON["messageId"]
        respx.get(f"{BASE}{MESSAGES_PATH}/{mid}").mock(return_value=httpx.Response(200, json=ATA_JSON))
        msg = await async_messages.get(mid)
        assert msg.type == "ata"

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_with_ata_mixed_in_does_not_raise(self, async_messages):
        respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [MESSAGE_JSON, ATA_JSON], "meta": {"total": 2, "page": 0, "pageSize": 20},
        }))
        page = await async_messages.list()
        assert [m.type for m in page] == ["sms", "ata"]


class TestAsyncMessagesCreateKakao:
    @respx.mock
    @pytest.mark.asyncio
    async def test_create_ata(self, async_messages):
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=ATA_JSON))
        msg = await async_messages.create(
            to="010", from_="070",
            kakao={"channel_id": "c1", "template_id": "t1", "variables": {"고객명": "홍길동"}},
            fallback={"body": "주문이 접수되었습니다."},
        )
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Kakao"]["Variables"] == {"고객명": "홍길동"}
        assert parsed["Fallback"] == {"Body": "주문이 접수되었습니다."}
        assert msg.type == "ata"

    @pytest.mark.asyncio
    async def test_body_with_kakao_rejected(self, async_messages):
        with pytest.raises(TypeError, match="함께 보낼 수 없는"):
            await async_messages.create(
                to="010", from_="070", body="안녕", kakao={"channel_id": "c1", "template_id": "t1"}
            )

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_bms(self, async_messages):
        """async 오버로드와 조립도 손으로 쓴 두 번째 벌이라 여기서만 실행된다."""
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(
            return_value=httpx.Response(201, json=BMS_JSON)
        )
        msg = await async_messages.create(
            to="01012345678", from_="07052358010",
            brand={"channel_id": "clx9kak0001", "template_id": "clx9bms0001"},
        )
        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Brand"] == {"ChannelId": "clx9kak0001", "TemplateId": "clx9bms0001"}
        assert msg.type == "bms"

    @pytest.mark.asyncio
    async def test_fallback_with_brand_rejected(self, async_messages):
        """거절은 HTTP 이전이다 — async 경로도 같은 규칙을 탄다."""
        with pytest.raises(TypeError, match="대체발송이 없습니다"):
            await async_messages.create(
                to="010", from_="070",
                brand={"channel_id": "c1", "template_id": "t1"},
                fallback={"body": "x"},
            )

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_ata_filter(self, async_messages):
        route = respx.get(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(200, json={
            "data": [], "meta": {"total": 0, "page": 0, "pageSize": 20},
        }))
        await async_messages.list(type="ata", number="07052358010")
        url = str(route.calls[0].request.url)
        assert "type=ata" in url
        assert "number=07052358010" in url


class TestBrandFreeForm:
    """자유형 — 템플릿 없이 말풍선을 직접 싣는다."""

    @respx.mock
    def test_free_body_passes_through_untouched(self, messages):
        """⛔ SDK 는 말풍선 안을 검사하지 않는다 — 표는 서버에 한 벌뿐이다."""
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=BMS_JSON))
        free = {
            "chatBubbleType": "CAROUSEL_FEED",
            "carousel": {"list": [{"header": "카드1", "imageId": "cmtn8o4vb000a"}]},
        }
        messages.create(to="010", from_="070", brand={"channel_id": "c1", "free": free})

        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Brand"] == {"ChannelId": "c1", "Free": free}

    @respx.mock
    def test_template_form_unchanged(self, messages):
        """자유형이 생겨도 템플릿형 몸통은 그대로다 — Free 키가 붙지 않는다."""
        route = respx.post(f"{BASE}{MESSAGES_PATH}").mock(return_value=httpx.Response(201, json=BMS_JSON))
        messages.create(to="010", from_="070", brand={"channel_id": "c1", "template_id": "t1"})

        parsed = json.loads(route.calls[0].request.content)
        assert parsed["Brand"] == {"ChannelId": "c1", "TemplateId": "t1"}

    def test_both_forms_rejected(self, messages):
        # ⛔ 서버와 같은 판정식이다 — 둘 다 실으면 어느 쪽으로 나갈지 정해 줄 수 없다.
        with pytest.raises(TypeError, match="하나만 지정"):
            messages.create(
                to="010", from_="070",
                brand={"channel_id": "c1", "template_id": "t1", "free": {"chatBubbleType": "TEXT"}},
            )

    def test_neither_form_rejected(self, messages):
        with pytest.raises(TypeError, match="하나만 지정"):
            messages.create(to="010", from_="070", brand={"channel_id": "c1"})

    def test_free_with_variables_rejected(self, messages):
        # 자유형엔 치환해 줄 템플릿이 없다 — `#{…}` 가 그대로 렌더된다.
        with pytest.raises(TypeError, match="variables"):
            messages.create(
                to="010", from_="070",
                brand={"channel_id": "c1", "free": {"chatBubbleType": "TEXT"}, "variables": {"고객명": "홍"}},
            )
