from __future__ import annotations

import time
from random import random
from typing import TYPE_CHECKING, Any, TypeVar, cast
from urllib.parse import urlparse

import httpx
import pydantic

from ._constants import (
    DEFAULT_BASE_URL,
    DEFAULT_CONNECTION_LIMITS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    INITIAL_RETRY_DELAY,
    MAX_RETRY_DELAY,
)
from ._exceptions import (
    APIConnectionError,
    APIResponseValidationError,
    APITimeoutError,
    _make_status_error,
)
from ._version import __version__

if TYPE_CHECKING:
    from .pagination import AsyncPage, SyncPage

_T = TypeVar("_T", bound=pydantic.BaseModel)

_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "[::1]"})


def _validate_base_url(url: str) -> str:
    """base_url이 HTTPS를 사용하는지 검증합니다. localhost는 HTTP를 허용합니다."""
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return url
    if parsed.scheme == "http" and parsed.hostname in _LOCALHOST_HOSTS:
        return url
    from ._exceptions import ClawOpsError

    raise ClawOpsError(
        f"base_url은 HTTPS를 사용해야 합니다 (받은 값: {url!r}). "
        f"로컬 개발 시에는 http://localhost를 사용할 수 있습니다."
    )


def _body_kwargs(
    body: dict[str, Any] | None,
    files: dict[str, Any] | None,
    headers: dict[str, str],
) -> dict[str, Any]:
    """본문을 httpx 인자로 옮긴다. 파일이 있으면 multipart 다.

    ⛔ multipart 는 boundary 가 붙은 Content-Type 을 **httpx 가 만든다.** 기본값
       (application/json)을 남겨 두면 그게 이기고 서버는 본문을 파싱하지 못하는데,
       그 실패는 "왜 파일이 안 왔지" 로만 드러난다. 그래서 헤더를 지우는 것과 본문을
       옮기는 것을 **한 곳에 묶었다** — 떨어져 있으면 한쪽만 고치는 날이 온다.
    """
    if not files:
        return {"json": body}
    headers.pop("Content-Type", None)
    return {"data": body, "files": files}


class SyncAPIClient:
    """동기 HTTP 클라이언트 베이스. httpx.Client를 래핑하며 인증, 재시도, 타임아웃, 에러 매핑을 처리합니다."""

    _client: httpx.Client
    _api_key: str
    _base_url: str
    _max_retries: int
    _timeout: httpx.Timeout

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.Client | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = _validate_base_url(base_url.rstrip("/"))
        self._max_retries = max_retries

        if isinstance(timeout, (int, float)):
            self._timeout = httpx.Timeout(timeout)
        else:
            self._timeout = timeout

        if http_client is not None:
            self._client = http_client
        else:
            self._client = httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout,
                limits=DEFAULT_CONNECTION_LIMITS,
            )

    def _build_headers(self, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"clawops-python/{__version__}",
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _send(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> httpx.Response:
        headers = self._build_headers(extra_headers)
        body_kwargs = _body_kwargs(body, files, headers)

        params = query.copy() if query else {}
        if extra_query:
            params.update(extra_query)

        req_timeout = timeout if timeout is not None else self._timeout
        if isinstance(req_timeout, (int, float)):
            req_timeout = httpx.Timeout(req_timeout)

        retry_budget = self._max_retries if max_retries is None else max_retries
        retries_left = retry_budget

        while True:
            try:
                response = self._client.request(
                    method=method,
                    url=path,
                    **body_kwargs,
                    params=params if params else None,
                    headers=headers,
                    timeout=req_timeout,
                )
            except httpx.TimeoutException as e:
                if retries_left > 0:
                    retries_left -= 1
                    time.sleep(self._retry_delay(retry_budget - retries_left))
                    continue
                raise APITimeoutError(request=httpx.Request(method, self._base_url + path)) from e
            except httpx.ConnectError as e:
                if retries_left > 0:
                    retries_left -= 1
                    time.sleep(self._retry_delay(retry_budget - retries_left))
                    continue
                raise APIConnectionError(request=httpx.Request(method, self._base_url + path)) from e

            if response.is_success:
                return response

            if retries_left > 0 and self._should_retry(response):
                retries_left -= 1
                time.sleep(self._retry_delay(retry_budget - retries_left))
                continue

            raise _make_status_error(response=response)

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        cast_to: type[_T] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> _T | None:
        response = self._send(
            method, path, body=body, query=query, files=files,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout, max_retries=max_retries,
        )
        if response.status_code == 204 or cast_to is None:
            return None
        try:
            return cast_to.model_validate(response.json())
        except pydantic.ValidationError as e:
            raise APIResponseValidationError(response=response) from e

    def _should_retry(self, response: httpx.Response) -> bool:
        if response.status_code in (408, 409, 429):
            return True
        if response.status_code >= 500:
            return True
        return False

    def _retry_delay(self, retries_taken: int) -> float:
        delay = min(INITIAL_RETRY_DELAY * (2 ** retries_taken), MAX_RETRY_DELAY)
        return delay * (1 + random())

    def _get(self, path: str, *, cast_to: type[_T], query: dict[str, Any] | None = None,
             extra_headers: dict[str, str] | None = None, extra_query: dict[str, object] | None = None,
             timeout: float | httpx.Timeout | None = None) -> _T:
        result = self._request("GET", path, cast_to=cast_to, query=query,
                               extra_headers=extra_headers, extra_query=extra_query, timeout=timeout)
        assert result is not None
        return result

    def _post(self, path: str, *, body: dict[str, Any] | None = None,
              files: dict[str, Any] | None = None, cast_to: type[_T],
              extra_headers: dict[str, str] | None = None, extra_query: dict[str, object] | None = None,
              timeout: float | httpx.Timeout | None = None, max_retries: int | None = None) -> _T:
        result = self._request("POST", path, body=body, files=files, cast_to=cast_to,
                               extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
                               max_retries=max_retries)
        assert result is not None
        return result

    def _put(self, path: str, *, body: dict[str, Any] | None = None, cast_to: type[_T],
             extra_headers: dict[str, str] | None = None, extra_query: dict[str, object] | None = None,
             timeout: float | httpx.Timeout | None = None) -> _T:
        result = self._request("PUT", path, body=body, cast_to=cast_to,
                               extra_headers=extra_headers, extra_query=extra_query, timeout=timeout)
        assert result is not None
        return result

    def _patch(self, path: str, *, body: dict[str, Any] | None = None, cast_to: type[_T],
               extra_headers: dict[str, str] | None = None, extra_query: dict[str, object] | None = None,
               timeout: float | httpx.Timeout | None = None) -> _T:
        result = self._request("PATCH", path, body=body, cast_to=cast_to,
                               extra_headers=extra_headers, extra_query=extra_query, timeout=timeout)
        assert result is not None
        return result

    def _delete(self, path: str, *, extra_headers: dict[str, str] | None = None,
                timeout: float | httpx.Timeout | None = None) -> None:
        self._request("DELETE", path, cast_to=None, extra_headers=extra_headers, timeout=timeout)

    def _delete_with_response(self, path: str, *, body: dict[str, Any] | None = None, cast_to: type[_T],
                              extra_headers: dict[str, str] | None = None,
                              extra_query: dict[str, object] | None = None,
                              timeout: float | httpx.Timeout | None = None) -> _T:
        """본문을 돌려주는 DELETE.

        ``_delete`` 는 응답을 버리는데, soft delete 처럼 삭제 결과 리소스를 그대로
        반환하는 endpoint 가 있어서 따로 둔다.
        """
        result = self._request("DELETE", path, body=body, cast_to=cast_to,
                               extra_headers=extra_headers, extra_query=extra_query, timeout=timeout)
        assert result is not None
        return result

    def _get_page(
        self,
        path: str,
        *,
        cast_to: type[_T],
        query: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> SyncPage[_T]:
        """목록 endpoint 전용 GET — 페이지 봉투와 **아이템 한 건 한 건**을 같은 try 안에서 검증한다.

        ⛔ 아이템 검증을 호출부에 두면 raw ``pydantic.ValidationError`` 가 그대로 새어 나가
        ``except ClawOpsError`` 로 잡히지 않는다. 서버가 어휘를 하나 늘리는 순간 목록 전체가
        **SDK 예외 계층 밖의 예외**로 죽는다. 페이지네이션 뒷장도 같은 경로를 탄다.
        """
        from .pagination import SyncPage

        response = self._send(
            "GET", path, query=query if query else None,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )
        try:
            page = SyncPage[cast_to].model_validate(response.json())  # type: ignore[valid-type]
            page.data = [
                cast_to.model_validate(item) if isinstance(item, dict) else item for item in page.data
            ]
        except pydantic.ValidationError as e:
            raise APIResponseValidationError(response=response) from e
        page._set_client(client=self, path=path, cast_to=cast_to, query=query or {})
        return cast("SyncPage[_T]", page)

    def _get_raw(
        self,
        path: str,
        *,
        extra_headers: dict[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response:
        """바이너리 응답(GET) 을 httpx.Response 그대로 반환. 4xx/5xx 는 에러로 매핑."""
        return self._send("GET", path, extra_headers=extra_headers, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SyncAPIClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class AsyncAPIClient:
    """비동기 HTTP 클라이언트 베이스. SyncAPIClient의 async 버전."""

    _client: httpx.AsyncClient
    _api_key: str
    _base_url: str
    _max_retries: int
    _timeout: httpx.Timeout

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.AsyncClient | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = _validate_base_url(base_url.rstrip("/"))
        self._max_retries = max_retries

        if isinstance(timeout, (int, float)):
            self._timeout = httpx.Timeout(timeout)
        else:
            self._timeout = timeout

        if http_client is not None:
            self._client = http_client
        else:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                limits=DEFAULT_CONNECTION_LIMITS,
            )

    def _build_headers(self, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"clawops-python/{__version__}",
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    async def _send(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> httpx.Response:
        import asyncio

        headers = self._build_headers(extra_headers)
        body_kwargs = _body_kwargs(body, files, headers)
        params = query.copy() if query else {}
        if extra_query:
            params.update(extra_query)

        req_timeout = timeout if timeout is not None else self._timeout
        if isinstance(req_timeout, (int, float)):
            req_timeout = httpx.Timeout(req_timeout)

        retry_budget = self._max_retries if max_retries is None else max_retries
        retries_left = retry_budget

        while True:
            try:
                response = await self._client.request(
                    method=method, url=path, **body_kwargs,
                    params=params if params else None,
                    headers=headers, timeout=req_timeout,
                )
            except httpx.TimeoutException as e:
                if retries_left > 0:
                    retries_left -= 1
                    await asyncio.sleep(self._retry_delay(retry_budget - retries_left))
                    continue
                raise APITimeoutError(request=httpx.Request(method, self._base_url + path)) from e
            except httpx.ConnectError as e:
                if retries_left > 0:
                    retries_left -= 1
                    await asyncio.sleep(self._retry_delay(retry_budget - retries_left))
                    continue
                raise APIConnectionError(request=httpx.Request(method, self._base_url + path)) from e

            if response.is_success:
                return response

            if retries_left > 0 and self._should_retry(response):
                retries_left -= 1
                await asyncio.sleep(self._retry_delay(retry_budget - retries_left))
                continue

            raise _make_status_error(response=response)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        cast_to: type[_T] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int | None = None,
    ) -> _T | None:
        response = await self._send(
            method, path, body=body, query=query, files=files,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout, max_retries=max_retries,
        )
        if response.status_code == 204 or cast_to is None:
            return None
        try:
            return cast_to.model_validate(response.json())
        except pydantic.ValidationError as e:
            raise APIResponseValidationError(response=response) from e

    def _should_retry(self, response: httpx.Response) -> bool:
        if response.status_code in (408, 409, 429):
            return True
        if response.status_code >= 500:
            return True
        return False

    def _retry_delay(self, retries_taken: int) -> float:
        delay = min(INITIAL_RETRY_DELAY * (2 ** retries_taken), MAX_RETRY_DELAY)
        return delay * (1 + random())

    async def _get(self, path: str, *, cast_to: type[_T], query: dict[str, Any] | None = None,
                   extra_headers: dict[str, str] | None = None, extra_query: dict[str, object] | None = None,
                   timeout: float | httpx.Timeout | None = None) -> _T:
        result = await self._request("GET", path, cast_to=cast_to, query=query,
                                     extra_headers=extra_headers, extra_query=extra_query, timeout=timeout)
        assert result is not None
        return result

    async def _post(self, path: str, *, body: dict[str, Any] | None = None,
                    files: dict[str, Any] | None = None, cast_to: type[_T],
                    extra_headers: dict[str, str] | None = None, extra_query: dict[str, object] | None = None,
                    timeout: float | httpx.Timeout | None = None, max_retries: int | None = None) -> _T:
        result = await self._request("POST", path, body=body, files=files, cast_to=cast_to,
                                     extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
                                     max_retries=max_retries)
        assert result is not None
        return result

    async def _put(self, path: str, *, body: dict[str, Any] | None = None, cast_to: type[_T],
                   extra_headers: dict[str, str] | None = None, extra_query: dict[str, object] | None = None,
                   timeout: float | httpx.Timeout | None = None) -> _T:
        result = await self._request("PUT", path, body=body, cast_to=cast_to,
                                     extra_headers=extra_headers, extra_query=extra_query, timeout=timeout)
        assert result is not None
        return result

    async def _patch(self, path: str, *, body: dict[str, Any] | None = None, cast_to: type[_T],
                     extra_headers: dict[str, str] | None = None, extra_query: dict[str, object] | None = None,
                     timeout: float | httpx.Timeout | None = None) -> _T:
        result = await self._request("PATCH", path, body=body, cast_to=cast_to,
                                     extra_headers=extra_headers, extra_query=extra_query, timeout=timeout)
        assert result is not None
        return result

    async def _delete(self, path: str, *, extra_headers: dict[str, str] | None = None,
                      timeout: float | httpx.Timeout | None = None) -> None:
        await self._request("DELETE", path, cast_to=None, extra_headers=extra_headers, timeout=timeout)

    async def _delete_with_response(self, path: str, *, body: dict[str, Any] | None = None, cast_to: type[_T],
                                    extra_headers: dict[str, str] | None = None,
                                    extra_query: dict[str, object] | None = None,
                                    timeout: float | httpx.Timeout | None = None) -> _T:
        """본문을 돌려주는 DELETE (sync 쪽 ``_delete_with_response`` 와 동일)."""
        result = await self._request("DELETE", path, body=body, cast_to=cast_to,
                                     extra_headers=extra_headers, extra_query=extra_query, timeout=timeout)
        assert result is not None
        return result

    async def _get_page(
        self,
        path: str,
        *,
        cast_to: type[_T],
        query: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_query: dict[str, object] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> AsyncPage[_T]:
        """:meth:`SyncAPIClient._get_page` 의 async 버전."""
        from .pagination import AsyncPage

        response = await self._send(
            "GET", path, query=query if query else None,
            extra_headers=extra_headers, extra_query=extra_query, timeout=timeout,
        )
        try:
            page = AsyncPage[cast_to].model_validate(response.json())  # type: ignore[valid-type]
            page.data = [
                cast_to.model_validate(item) if isinstance(item, dict) else item for item in page.data
            ]
        except pydantic.ValidationError as e:
            raise APIResponseValidationError(response=response) from e
        page._set_client(client=self, path=path, cast_to=cast_to, query=query or {})
        return cast("AsyncPage[_T]", page)

    async def _get_raw(
        self,
        path: str,
        *,
        extra_headers: dict[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response:
        """바이너리 응답(GET) 을 httpx.Response 그대로 반환. 4xx/5xx 는 에러로 매핑."""
        return await self._send("GET", path, extra_headers=extra_headers, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncAPIClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
