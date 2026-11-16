from __future__ import annotations

import os

import httpx

from ._base_client import AsyncAPIClient, SyncAPIClient
from ._constants import DEFAULT_BASE_URL, DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT
from ._exceptions import ClawOpsError
from .resources.accounts import AccountContext, AsyncAccountContext
from .resources.calls import AsyncCalls, Calls
from .resources.kakao import AsyncKakao, Kakao
from .resources.messages import AsyncMessages, Messages
from .resources.numbers import AsyncNumbers, Numbers
from .resources.recordings import AsyncRecordings, Recordings
from .resources.assignment_links import AssignmentLinks, AsyncAssignmentLinks
from .resources.blocked_recipients import AsyncBlockedRecipients, BlockedRecipients
from .resources.sender_numbers import AsyncSenderNumbers, SenderNumbers
from .resources.sip_credentials import AsyncSipCredentials, SipCredentials
from .resources.sip_endpoints import AsyncSipEndpoints, SipEndpoints
from .resources.webhook_logs import AsyncWebhookLogs, WebhookLogs
from .webhooks import Webhooks


class ClawOps(SyncAPIClient):
    """ClawOps Voice API의 동기 클라이언트.

    Example::

        from clawops import ClawOps

        client = ClawOps(api_key="sk_...", account_id="AC1a2b3c4d")

        call = client.calls.create(
            to="01012345678", from_="07052358010",
            url="https://my-app.com/twiml",
        )

        other = client.accounts("AC_other")
        other.calls.list()

    Args:
        api_key: API 키 (sk_...). 생략 시 CLAWOPS_API_KEY 환경변수 사용.
        account_id: 기본 계정 ID (AC...). 생략 시 CLAWOPS_ACCOUNT_ID 환경변수 사용.
        base_url: API 기본 URL. 기본값: https://api.koreaaitel.com
        timeout: 요청 타임아웃 (초 또는 httpx.Timeout). 기본값: 600초.
        max_retries: 최대 재시도 횟수. 기본값: 2.
        http_client: 커스텀 httpx.Client 인스턴스.
        default_headers: 모든 요청에 포함할 기본 HTTP 헤더.
    """

    _default_account_id: str

    def __init__(
        self,
        *,
        api_key: str | None = None,
        account_id: str | None = None,
        base_url: str | None = None,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.Client | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        if api_key is None:
            api_key = os.environ.get("CLAWOPS_API_KEY")
        if api_key is None:
            raise ClawOpsError("api_key를 지정하거나 CLAWOPS_API_KEY 환경변수를 설정하세요.")

        if account_id is None:
            account_id = os.environ.get("CLAWOPS_ACCOUNT_ID")
        if account_id is None:
            raise ClawOpsError("account_id를 지정하거나 CLAWOPS_ACCOUNT_ID 환경변수를 설정하세요.")

        if base_url is None:
            base_url = os.environ.get("CLAWOPS_BASE_URL", DEFAULT_BASE_URL)

        self._default_account_id = account_id

        super().__init__(
            api_key=api_key, base_url=base_url, timeout=timeout,
            max_retries=max_retries, http_client=http_client, default_headers=default_headers,
        )

    @property
    def calls(self) -> Calls:
        """통화(Calls) 리소스에 접근합니다."""
        return Calls(client=self, account_id=self._default_account_id)

    @property
    def numbers(self) -> Numbers:
        """전화번호(Numbers) 리소스에 접근합니다."""
        return Numbers(client=self, account_id=self._default_account_id)

    @property
    def sip_credentials(self) -> SipCredentials:
        """SIP 단말(credential) 리소스에 접근합니다 (조회 전용)."""
        return SipCredentials(client=self, account_id=self._default_account_id)

    @property
    def sip_endpoints(self) -> SipEndpoints:
        """SIP 엔드포인트 리소스에 접근합니다 (조회 전용)."""
        return SipEndpoints(client=self, account_id=self._default_account_id)

    @property
    def recordings(self) -> Recordings:
        """통화 녹음(Recordings) 리소스에 접근합니다."""
        return Recordings(client=self, account_id=self._default_account_id)

    @property
    def messages(self) -> Messages:
        """메시지(Messages) 리소스에 접근합니다."""
        return Messages(client=self, account_id=self._default_account_id)

    @property
    def kakao(self) -> Kakao:
        """카카오 알림톡 리소스(채널·템플릿·카테고리)에 접근합니다.

        발송 자체는 ``client.messages.create(..., kakao={...})`` 입니다.
        """
        return Kakao(client=self, account_id=self._default_account_id)

    @property
    def webhook_logs(self) -> WebhookLogs:
        """Webhook 발송 로그 리소스에 접근합니다."""
        return WebhookLogs(client=self, account_id=self._default_account_id)

    @property
    def assignment_links(self) -> AssignmentLinks:
        """관리번호(External Assignment) 발급 링크 리소스에 접근합니다."""
        return AssignmentLinks(client=self, account_id=self._default_account_id)

    @property
    def blocked_recipients(self) -> BlockedRecipients:
        """수신거부(DNC) 명단 리소스에 접근합니다."""
        return BlockedRecipients(client=self, account_id=self._default_account_id)

    @property
    def sender_numbers(self) -> SenderNumbers:
        """문자 발신번호 리소스에 접근합니다(조회·등록·삭제)."""
        return SenderNumbers(client=self, account_id=self._default_account_id)

    @property
    def webhooks(self) -> Webhooks:
        """Webhook 서명 검증 유틸리티."""
        return Webhooks()

    def accounts(self, account_id: str) -> AccountContext:
        """다른 계정의 리소스에 접근합니다.

        Args:
            account_id: 접근할 계정 ID.

        Returns:
            해당 계정에 바인딩된 AccountContext 객체.
        """
        return AccountContext(client=self, account_id=account_id)


class AsyncClawOps(AsyncAPIClient):
    """ClawOps Voice API의 비동기 클라이언트.

    Example::

        async with AsyncClawOps(api_key="sk_...", account_id="AC...") as client:
            call = await client.calls.create(
                to="01012345678", from_="07052358010",
                url="https://my-app.com/twiml",
            )
    """

    _default_account_id: str

    def __init__(
        self,
        *,
        api_key: str | None = None,
        account_id: str | None = None,
        base_url: str | None = None,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.AsyncClient | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        if api_key is None:
            api_key = os.environ.get("CLAWOPS_API_KEY")
        if api_key is None:
            raise ClawOpsError("api_key를 지정하거나 CLAWOPS_API_KEY 환경변수를 설정하세요.")

        if account_id is None:
            account_id = os.environ.get("CLAWOPS_ACCOUNT_ID")
        if account_id is None:
            raise ClawOpsError("account_id를 지정하거나 CLAWOPS_ACCOUNT_ID 환경변수를 설정하세요.")

        if base_url is None:
            base_url = os.environ.get("CLAWOPS_BASE_URL", DEFAULT_BASE_URL)

        self._default_account_id = account_id

        super().__init__(
            api_key=api_key, base_url=base_url, timeout=timeout,
            max_retries=max_retries, http_client=http_client, default_headers=default_headers,
        )

    @property
    def calls(self) -> AsyncCalls:
        return AsyncCalls(client=self, account_id=self._default_account_id)

    @property
    def numbers(self) -> AsyncNumbers:
        return AsyncNumbers(client=self, account_id=self._default_account_id)

    @property
    def sip_credentials(self) -> AsyncSipCredentials:
        return AsyncSipCredentials(client=self, account_id=self._default_account_id)

    @property
    def sip_endpoints(self) -> AsyncSipEndpoints:
        return AsyncSipEndpoints(client=self, account_id=self._default_account_id)

    @property
    def recordings(self) -> AsyncRecordings:
        return AsyncRecordings(client=self, account_id=self._default_account_id)

    @property
    def messages(self) -> AsyncMessages:
        return AsyncMessages(client=self, account_id=self._default_account_id)

    @property
    def kakao(self) -> AsyncKakao:
        """카카오 알림톡 리소스(채널·템플릿·카테고리)에 접근합니다."""
        return AsyncKakao(client=self, account_id=self._default_account_id)

    @property
    def webhook_logs(self) -> AsyncWebhookLogs:
        """Webhook 발송 로그 리소스에 접근합니다."""
        return AsyncWebhookLogs(client=self, account_id=self._default_account_id)

    @property
    def assignment_links(self) -> AsyncAssignmentLinks:
        """관리번호 발급 링크 리소스에 접근합니다."""
        return AsyncAssignmentLinks(client=self, account_id=self._default_account_id)

    @property
    def blocked_recipients(self) -> AsyncBlockedRecipients:
        """수신거부(DNC) 명단 리소스에 접근합니다."""
        return AsyncBlockedRecipients(client=self, account_id=self._default_account_id)

    @property
    def sender_numbers(self) -> AsyncSenderNumbers:
        """문자 발신번호 리소스에 접근합니다(조회·등록·삭제)."""
        return AsyncSenderNumbers(client=self, account_id=self._default_account_id)

    @property
    def webhooks(self) -> Webhooks:
        return Webhooks()

    def accounts(self, account_id: str) -> AsyncAccountContext:
        return AsyncAccountContext(client=self, account_id=account_id)
