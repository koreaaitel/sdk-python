import httpx
import pytest
import respx

from clawops._base_client import SyncAPIClient, AsyncAPIClient
from clawops._constants import DEFAULT_BASE_URL, DEFAULT_TIMEOUT
from clawops._exceptions import AuthenticationError, ClawOpsError, NotFoundError
from clawops._models import BaseModel


class DummyResponse(BaseModel):
    message: str


class TestSyncAPIClient:
    def _make_client(self, **kwargs) -> SyncAPIClient:
        return SyncAPIClient(
            api_key=kwargs.get("api_key", "sk_test_key"),
            base_url=kwargs.get("base_url", DEFAULT_BASE_URL),
            timeout=kwargs.get("timeout", DEFAULT_TIMEOUT),
            max_retries=kwargs.get("max_retries", 0),
        )

    def test_default_headers(self):
        client = self._make_client()
        headers = client._build_headers()
        assert headers["Authorization"] == "Bearer sk_test_key"
        assert "clawops-python/" in headers["User-Agent"]
        assert headers["Content-Type"] == "application/json"
        client.close()

    @respx.mock
    def test_get_success(self):
        respx.get("https://api.koreaaitel.com/test").mock(
            return_value=httpx.Response(200, json={"message": "ok"})
        )
        client = self._make_client()
        result = client._get("/test", cast_to=DummyResponse)
        assert isinstance(result, DummyResponse)
        assert result.message == "ok"
        client.close()

    @respx.mock
    def test_post_success(self):
        respx.post("https://api.koreaaitel.com/test").mock(
            return_value=httpx.Response(201, json={"message": "created"})
        )
        client = self._make_client()
        result = client._post("/test", body={"key": "val"}, cast_to=DummyResponse)
        assert result.message == "created"
        client.close()

    @respx.mock
    def test_404_raises_not_found(self):
        respx.get("https://api.koreaaitel.com/test").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )
        client = self._make_client()
        with pytest.raises(NotFoundError) as exc_info:
            client._get("/test", cast_to=DummyResponse)
        assert exc_info.value.status_code == 404
        assert exc_info.value.body == {"error": "not found"}
        client.close()

    @respx.mock
    def test_401_raises_authentication_error(self):
        respx.get("https://api.koreaaitel.com/test").mock(
            return_value=httpx.Response(401, json={"error": "invalid key"})
        )
        client = self._make_client()
        with pytest.raises(AuthenticationError):
            client._get("/test", cast_to=DummyResponse)
        client.close()

    @respx.mock
    def test_retry_on_500(self):
        route = respx.get("https://api.koreaaitel.com/test")
        route.side_effect = [
            httpx.Response(500, json={"error": "server error"}),
            httpx.Response(200, json={"message": "ok"}),
        ]
        client = self._make_client(max_retries=1)
        result = client._get("/test", cast_to=DummyResponse)
        assert result.message == "ok"
        assert route.call_count == 2
        client.close()

    @respx.mock
    def test_delete_returns_none(self):
        respx.delete("https://api.koreaaitel.com/test").mock(
            return_value=httpx.Response(204)
        )
        client = self._make_client()
        result = client._delete("/test")
        assert result is None
        client.close()

    def test_context_manager(self):
        with self._make_client() as client:
            assert client is not None

    @respx.mock
    def test_extra_headers_override(self):
        route = respx.get("https://api.koreaaitel.com/test").mock(
            return_value=httpx.Response(200, json={"message": "ok"})
        )
        client = self._make_client()
        client._get("/test", cast_to=DummyResponse, extra_headers={"X-Custom": "value"})
        assert route.calls[0].request.headers["X-Custom"] == "value"
        client.close()

    @respx.mock
    def test_extra_query_params(self):
        route = respx.get("https://api.koreaaitel.com/test").mock(
            return_value=httpx.Response(200, json={"message": "ok"})
        )
        client = self._make_client()
        client._get("/test", cast_to=DummyResponse, extra_query={"foo": "bar"})
        assert "foo=bar" in str(route.calls[0].request.url)
        client.close()


class TestAsyncAPIClient:
    def _make_client(self, **kwargs) -> AsyncAPIClient:
        return AsyncAPIClient(
            api_key=kwargs.get("api_key", "sk_test_key"),
            base_url=kwargs.get("base_url", DEFAULT_BASE_URL),
            timeout=kwargs.get("timeout", DEFAULT_TIMEOUT),
            max_retries=kwargs.get("max_retries", 0),
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_success(self):
        respx.get("https://api.koreaaitel.com/test").mock(
            return_value=httpx.Response(200, json={"message": "ok"})
        )
        client = self._make_client()
        result = await client._get("/test", cast_to=DummyResponse)
        assert result.message == "ok"
        await client.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_404_raises_not_found(self):
        respx.get("https://api.koreaaitel.com/test").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )
        client = self._make_client()
        with pytest.raises(NotFoundError):
            await client._get("/test", cast_to=DummyResponse)
        await client.close()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with self._make_client() as client:
            assert client is not None


class TestBaseUrlValidation:
    def test_https_allowed(self):
        client = SyncAPIClient(api_key="sk_test", base_url="https://api.example.com")
        assert client._base_url == "https://api.example.com"
        client.close()

    def test_http_localhost_allowed(self):
        client = SyncAPIClient(api_key="sk_test", base_url="http://localhost:8080")
        assert client._base_url == "http://localhost:8080"
        client.close()

    def test_http_127_allowed(self):
        client = SyncAPIClient(api_key="sk_test", base_url="http://127.0.0.1:3000")
        assert client._base_url == "http://127.0.0.1:3000"
        client.close()

    def test_http_remote_rejected(self):
        with pytest.raises(ClawOpsError, match="HTTPS"):
            SyncAPIClient(api_key="sk_test", base_url="http://api.example.com")

    def test_ftp_rejected(self):
        with pytest.raises(ClawOpsError, match="HTTPS"):
            SyncAPIClient(api_key="sk_test", base_url="ftp://api.example.com")

    @pytest.mark.asyncio
    async def test_async_http_remote_rejected(self):
        with pytest.raises(ClawOpsError, match="HTTPS"):
            AsyncAPIClient(api_key="sk_test", base_url="http://evil.example.com")
