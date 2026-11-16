import httpx
import pytest
import respx

from clawops._base_client import AsyncAPIClient, SyncAPIClient
from clawops.resources.recordings import Recordings, AsyncRecordings


BASE = "https://api.koreaaitel.com"
ACCOUNT = "AC1a2b3c4d"
RECORDINGS_PATH = f"/v1/accounts/{ACCOUNT}/recordings"


@pytest.fixture
def client():
    c = SyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)
    yield c
    c.close()


@pytest.fixture
def recordings(client):
    return Recordings(client=client, account_id=ACCOUNT)


class TestRecordingsDelete:
    @respx.mock
    def test_delete(self, recordings):
        respx.delete(f"{BASE}{RECORDINGS_PATH}/CAabc123").mock(return_value=httpx.Response(204))
        result = recordings.delete("CAabc123")
        assert result is None

    @respx.mock
    def test_delete_idempotent(self, recordings):
        """녹음이 이미 없어도 성공(멱등)."""
        respx.delete(f"{BASE}{RECORDINGS_PATH}/CAnonexistent").mock(return_value=httpx.Response(204))
        result = recordings.delete("CAnonexistent")
        assert result is None

    @respx.mock
    def test_delete_calls_correct_path(self, recordings):
        route = respx.delete(f"{BASE}{RECORDINGS_PATH}/CAabc123").mock(return_value=httpx.Response(204))
        recordings.delete("CAabc123")
        assert route.called
        assert route.call_count == 1


@pytest.fixture
async def async_client():
    c = AsyncAPIClient(api_key="sk_test", base_url=BASE, max_retries=0)
    yield c
    await c.close()


@pytest.fixture
def async_recordings(async_client):
    return AsyncRecordings(client=async_client, account_id=ACCOUNT)


class TestAsyncRecordingsDelete:
    @respx.mock
    @pytest.mark.asyncio
    async def test_delete(self, async_recordings):
        route = respx.delete(f"{BASE}{RECORDINGS_PATH}/CAabc123").mock(return_value=httpx.Response(204))
        result = await async_recordings.delete("CAabc123")
        assert result is None
        assert route.called
        assert route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_delete_idempotent(self, async_recordings):
        """녹음이 이미 없어도 성공(멱등)."""
        respx.delete(f"{BASE}{RECORDINGS_PATH}/CAnonexistent").mock(return_value=httpx.Response(204))
        result = await async_recordings.delete("CAnonexistent")
        assert result is None
