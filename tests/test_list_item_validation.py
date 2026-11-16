"""목록 아이템 검증이 SDK 예외 계층 안에서 도는지 — **페이지형 목록 전부**.

서버가 응답 어휘를 하나 늘리면 그 값이 섞인 목록은 통째로 죽는다. 그 자체는 어쩔 수
없지만, 죽는 **방식**은 계약이다. 아이템 검증이 클라이언트의 try 밖에서 돌던 동안에는
raw ``pydantic.ValidationError`` 가 새어 나가 ``except ClawOpsError`` 로도 잡히지
않았다. 실제로 알림톡(``type="ata"``)이 그렇게 터졌다.

리소스마다 같은 모양의 코드가 sync/async 로 복사돼 있어 한 곳만 고치면 나머지가 남는다.
그래서 여기서는 **페이지를 돌려주는 목록 전부**를 같은 규칙으로 건다.
"""

from __future__ import annotations

import httpx
import pydantic
import pytest
import respx

from clawops import AsyncClawOps, ClawOps
from clawops._exceptions import APIResponseValidationError, ClawOpsError

BASE = "https://api.koreaaitel.com"
ACCOUNT = "AC1a2b3c4d"
ROOT = f"{BASE}/v1/accounts/{ACCOUNT}"

# 어느 모델로도 검증되지 않는 아이템. "서버가 모르는 값을 보냈다" 의 최소 재현이다.
BAD_ITEM = {"unexpected": True}
META = {"total": 1, "page": 0, "pageSize": 20}


def _page(*items: object) -> httpx.Response:
    return httpx.Response(200, json={"data": list(items), "meta": META})


# (이름, 경로, sync 호출, async 호출)
LISTS = [
    ("calls", f"{ROOT}/calls", lambda c: c.calls.list(), lambda c: c.calls.list()),
    ("messages", f"{ROOT}/messages", lambda c: c.messages.list(), lambda c: c.messages.list()),
    (
        "kakao.channels",
        f"{ROOT}/kakao/channels",
        lambda c: c.kakao.channels.list(),
        lambda c: c.kakao.channels.list(),
    ),
    (
        "kakao.templates",
        f"{ROOT}/kakao/templates",
        lambda c: c.kakao.templates.list(channel_id="clx9kak0001"),
        lambda c: c.kakao.templates.list(channel_id="clx9kak0001"),
    ),
    (
        "assignment_links",
        f"{ROOT}/assignment-links",
        lambda c: c.assignment_links.list(),
        lambda c: c.assignment_links.list(),
    ),
    (
        "blocked_recipients",
        f"{ROOT}/blocked-recipients",
        lambda c: c.blocked_recipients.list(),
        lambda c: c.blocked_recipients.list(),
    ),
    (
        "webhook_logs",
        f"{ROOT}/webhooks/WH0001/logs",
        lambda c: c.webhook_logs.list("WH0001"),
        lambda c: c.webhook_logs.list("WH0001"),
    ),
]

IDS = [name for name, _, _, _ in LISTS]


@pytest.fixture
def client(api_key, account_id):
    c = ClawOps(api_key=api_key, account_id=account_id, max_retries=0)
    yield c
    c.close()


@pytest.mark.parametrize("name,path,call,_acall", LISTS, ids=IDS)
@respx.mock
def test_bad_item_raises_clawops_error(client, name, path, call, _acall):
    respx.get(path).mock(return_value=_page(BAD_ITEM))

    with pytest.raises(APIResponseValidationError) as exc:
        call(client)

    # 핵심은 타입이다. raw pydantic 예외면 사용자의 except 가 통과시킨다.
    assert isinstance(exc.value, ClawOpsError)
    assert not isinstance(exc.value, pydantic.ValidationError)


@pytest.mark.parametrize("name,path,_call,acall", LISTS, ids=IDS)
@pytest.mark.asyncio
@respx.mock
async def test_bad_item_raises_clawops_error_async(api_key, account_id, name, path, _call, acall):
    respx.get(path).mock(return_value=_page(BAD_ITEM))

    async with AsyncClawOps(api_key=api_key, account_id=account_id, max_retries=0) as client:
        with pytest.raises(APIResponseValidationError) as exc:
            await acall(client)

    assert isinstance(exc.value, ClawOpsError)
    assert not isinstance(exc.value, pydantic.ValidationError)


@respx.mock
def test_bad_item_on_second_page_raises_clawops_error(client):
    """뒷장도 같은 경로를 탄다.

    첫 장만 고치면 ``auto_paging_iter()`` 가 중간에 SDK 밖의 예외로 끊긴다 —
    부분적으로 받아 놓은 결과까지 같이 날아가므로 첫 장보다 오히려 나쁘다.
    """
    good = {
        "callId": "CA0123456789abcdef0123456789abcdef",
        "accountId": ACCOUNT,
        "from": "07052358010",
        "to": "01012345678",
        "status": "completed",
        "direction": "outbound",
        "duration": None,
        "dateCreated": "2026-09-02T00:00:00Z",
        "dateUpdated": None,
    }
    respx.get(f"{ROOT}/calls").mock(
        side_effect=[
            httpx.Response(200, json={"data": [good], "meta": {"total": 2, "page": 0, "pageSize": 1}}),
            httpx.Response(200, json={"data": [BAD_ITEM], "meta": {"total": 2, "page": 1, "pageSize": 1}}),
        ]
    )

    it = client.calls.list(page_size=1).auto_paging_iter()
    assert next(it) is not None

    with pytest.raises(APIResponseValidationError) as exc:
        next(it)
    assert isinstance(exc.value, ClawOpsError)
