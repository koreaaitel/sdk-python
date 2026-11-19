import asyncio
import struct
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from clawops.agent._session import CallSession
from clawops.agent.pipeline import PipelineSession, SpeechEvent, TypecastTTS
from clawops.agent.pipeline.tts import _typecast


def _wav(pcm, *, rate=32000, data_size=0xFFFFFFFF):
    format_chunk = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    extra_chunk = b"JUNK" + struct.pack("<I", 3) + b"abc\x00"
    return (
        b"RIFF\xff\xff\xff\xffWAVE"
        + format_chunk
        + extra_chunk
        + b"data"
        + struct.pack("<I", data_size)
        + pcm
    )


async def _iterate(*values):
    for value in values:
        yield value


@pytest.fixture
def typecast_http(monkeypatch):
    requests = []
    responses = []
    closed = []

    @asynccontextmanager
    async def post(url, **kwargs):
        requests.append((url, kwargs))
        response = SimpleNamespace(
            status=200,
            content=SimpleNamespace(iter_chunked=lambda _: responses.pop(0)),
            raise_for_status=lambda: None,
        )
        try:
            yield response
        finally:
            closed.append("response")

    @asynccontextmanager
    async def client_session(**kwargs):
        try:
            yield SimpleNamespace(post=post)
        finally:
            closed.append("session")

    monkeypatch.setattr(_typecast.aiohttp, "ClientSession", client_session)
    return SimpleNamespace(requests=requests, responses=responses, closed=closed)


async def test_streams_fragmented_wav_and_keeps_pcm_aligned(typecast_http, monkeypatch):
    monkeypatch.setenv("TYPECAST_API_KEY", "typecast-test-key")
    pcm = bytes(range(26))
    for portion in (pcm[:10], pcm[10:]):
        wav = _wav(portion)
        typecast_http.responses.append(_iterate(*(wav[i:i + 3] for i in range(0, len(wav), 3))))

    tts = TypecastTTS(voice="tc_test", speed=0.8)
    stream = tts.synthesize(_iterate(" ", "첫 문장.", "다음 문장."))
    chunks = [chunk async for chunk in stream]

    assert b"".join(chunks) == pcm
    assert all(len(chunk) % 8 == 0 for chunk in chunks[:-1])
    assert len(chunks[-1]) == 2
    assert (tts.provider, tts.model, tts.voice_id, tts.sample_rate) == ("typecast", "ssfm-v31", "tc_test", 32000)
    for (url, request), text in zip(typecast_http.requests, ("첫 문장.", "다음 문장.")):
        assert url == "https://api.typecast.ai/v1/text-to-speech/stream"
        assert request["headers"] == {"X-API-KEY": "typecast-test-key"}
        assert request["allow_redirects"] is False
        assert request["json"] == {
            "voice_id": "tc_test",
            "model": "ssfm-v31",
            "language": "kor",
            "output": {"audio_format": "wav", "audio_tempo": 0.8},
            "text": text,
        }
    assert typecast_http.closed == ["response", "response", "session"]


async def test_caps_text_at_provider_limit_and_accepts_finite_wav(typecast_http):
    pcm = bytes(8)
    wav = _wav(pcm, data_size=len(pcm)) + b"ignored after the data chunk"
    typecast_http.responses.extend((_iterate(wav), _iterate(wav)))
    tts = TypecastTTS(voice="uc_owned", language=None, api_key="test-key")

    chunks = [chunk async for chunk in tts.synthesize(_iterate("한" * 2000 + " 😄"))]

    assert chunks == [pcm, pcm]
    bodies = [request["json"] for _, request in typecast_http.requests]
    assert [body["text"] for body in bodies] == ["한" * 2000, "😄"]
    assert all("language" not in body for body in bodies)


@pytest.mark.parametrize(
    ("wav", "message"),
    [
        (_wav(bytes(8), rate=24000), "mono 32 kHz PCM16"),
        (_wav(bytes(6), data_size=8), "empty or truncated"),
        (_wav(bytes(7)), "empty or truncated"),
    ],
)
async def test_rejects_wrong_or_truncated_audio_and_closes_request(typecast_http, wav, message):
    typecast_http.responses.append(_iterate(wav))
    tts = TypecastTTS(voice="tc_test", api_key="test-key")

    with pytest.raises(ValueError, match=message):
        async for _ in tts.synthesize(_iterate("문장")):
            pass

    assert typecast_http.closed == ["response", "session"]


async def test_pipeline_barge_in_closes_typecast_while_audio_send_is_blocked(typecast_http):
    wav = _wav(bytes(16))
    typecast_http.responses.append(_iterate(wav[:-8], wav[-8:]))
    sending = asyncio.Event()
    cleared = asyncio.Event()
    sent = []
    closed_when_cleared = []

    async def send_audio(audio):
        sent.append(audio)
        if len(sent) == 2:
            sending.set()
            await asyncio.Future()

    async def clear_audio():
        closed_when_cleared.extend(typecast_http.closed)
        cleared.set()

    async def transcribe(audio_stream):
        async for _ in audio_stream:
            yield SpeechEvent(type="interim", transcript="잠시만요")

    async def generate(messages, tools=None):
        yield "안녕하세요."

    call = CallSession(call_id="local", from_number="010", to_number="070", account_id="AC")
    call._send_audio_fn = send_audio
    call._send_clear_fn = clear_audio
    pipeline = PipelineSession(
        stt=SimpleNamespace(transcribe=transcribe),
        llm=SimpleNamespace(generate=generate),
        tts=TypecastTTS(voice="tc_test", api_key="test-key"),
    )
    await pipeline.start(call)
    try:
        await asyncio.wait_for(sending.wait(), timeout=2)
        await pipeline.feed_audio(b"\xff" * 160, 0)
        await asyncio.wait_for(cleared.wait(), timeout=2)

        assert sent == [b"\xff", b"\xff"]
        assert closed_when_cleared == ["response", "session"]
    finally:
        await pipeline.stop()
