import httpx
import pytest
import respx

from clawops import ClawOps, AsyncClawOps
from clawops.resources.calls import Calls, AsyncCalls
from clawops.resources.numbers import Numbers, AsyncNumbers
from clawops._exceptions import ClawOpsError

BASE = "https://api.koreaaitel.com"


class TestClawOpsClient:
    def test_basic_init(self):
        client = ClawOps(api_key="sk_test", account_id="AC123")
        assert isinstance(client.calls, Calls)
        assert isinstance(client.numbers, Numbers)
        client.close()

    def test_env_var_init(self, monkeypatch):
        monkeypatch.setenv("CLAWOPS_API_KEY", "sk_env")
        monkeypatch.setenv("CLAWOPS_ACCOUNT_ID", "AC_env")
        client = ClawOps()
        assert client._api_key == "sk_env"
        assert client._default_account_id == "AC_env"
        client.close()

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("CLAWOPS_API_KEY", raising=False)
        with pytest.raises(ClawOpsError, match="api_key"):
            ClawOps(account_id="AC123")

    def test_missing_account_id(self, monkeypatch):
        monkeypatch.delenv("CLAWOPS_ACCOUNT_ID", raising=False)
        with pytest.raises(ClawOpsError, match="account_id"):
            ClawOps(api_key="sk_test")

    def test_accounts(self):
        client = ClawOps(api_key="sk_test", account_id="AC_main")
        other = client.accounts("AC_other")
        assert client.calls._account_id == "AC_main"
        assert other.calls._account_id == "AC_other"
        client.close()

    def test_context_manager(self):
        with ClawOps(api_key="sk_test", account_id="AC123") as client:
            assert isinstance(client.calls, Calls)

    @respx.mock
    def test_end_to_end(self):
        respx.post(f"{BASE}/v1/accounts/AC123/calls").mock(
            return_value=httpx.Response(201, json={
                "callId": "CA_test", "status": "queued", "to": "010", "from": "070",
                "direction": "outbound", "accountId": "AC123", "dateCreated": "2025-01-01T00:00:00Z",
            })
        )
        with ClawOps(api_key="sk_test", account_id="AC123", max_retries=0) as client:
            call = client.calls.create(to="010", from_="070", url="https://t.com")
            assert call.call_id == "CA_test"


class TestAsyncClawOps:
    @pytest.mark.asyncio
    async def test_basic_init(self):
        client = AsyncClawOps(api_key="sk_test", account_id="AC123")
        assert isinstance(client.calls, AsyncCalls)
        assert isinstance(client.numbers, AsyncNumbers)
        await client.close()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with AsyncClawOps(api_key="sk_test", account_id="AC123") as client:
            assert isinstance(client.calls, AsyncCalls)

    @pytest.mark.asyncio
    async def test_accounts(self):
        async with AsyncClawOps(api_key="sk_test", account_id="AC_main") as client:
            other = client.accounts("AC_other")
            assert other.calls._account_id == "AC_other"
