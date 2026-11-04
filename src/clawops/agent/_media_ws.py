"""Media WebSocket: per-call 오디오 스트림.

ClawOps VoiceML Stream 프로토콜을 구현한다:
connected -> start -> media (G.711 ulaw 8kHz base64) -> mark -> stop
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import aiohttp

from .media import MediaProfile

log = logging.getLogger("clawops.agent")

# 죽은 피어(FIN/RST 없이 사라진 TCP: NAT idle timeout·네트워크 분단·콜노드 강제종료)를
# 감지하려면 WS keepalive 가 필요하다. 없으면 `stop` 프레임이 영영 안 와 수신 루프가 무한
# 블록되고, 서버 러너에서는 그 워커 슬롯이 영구 점유된다. aiohttp 는 ping 을 보내고 pong 이
# 이 시간 내 안 오면 연결을 닫는다(수신 루프가 깨져 정상 정리로 이어짐).
MEDIA_WS_HEARTBEAT_S = 30.0


def parse_start_event(data: dict[str, Any]) -> dict[str, Any]:
    start = data["start"]
    fmt = start.get("mediaFormat", {})
    return {
        "stream_id": start["streamId"],
        "call_id": start["callId"],
        "account_id": start.get("accountId", ""),
        "sample_rate": fmt.get("sampleRate", 8000),
    }


def parse_media_event(data: dict[str, Any]) -> dict[str, Any]:
    media = data["media"]
    return {
        "audio": base64.b64decode(media["payload"]),
        "timestamp": int(media.get("timestamp", 0)),
    }


def build_media_response(audio: bytes) -> dict[str, Any]:
    return {
        "event": "media",
        "media": {
            "payload": base64.b64encode(audio).decode(),
        },
    }


VALID_DTMF_DIGITS = set("0123456789*#")


def build_dtmf_message(digit: str) -> dict[str, Any]:
    if digit not in VALID_DTMF_DIGITS:
        raise ValueError(f"유효하지 않은 DTMF digit: {digit}")
    return {"event": "dtmf", "dtmf": {"digit": digit}}


def parse_dtmf_event(data: dict[str, Any]) -> dict[str, Any]:
    dtmf = data["dtmf"]
    return {
        "digit": dtmf["digit"],
        "track": dtmf.get("track", ""),
    }


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _warn_if_plaintext(url: str) -> None:
    """공개 호스트로 가는 ``ws://`` 면 경고한다 — 연결은 막지 않는다(로컬 하네스는 평문을 쓴다)."""
    parsed = urlparse(url)
    if parsed.scheme == "ws" and parsed.hostname not in _LOOPBACK_HOSTS:
        log.warning(
            "Media WS URL is plaintext (ws://%s); the call token travels unencrypted. "
            "ClawOps sends wss:// URLs — check what rewrote this one.",
            parsed.hostname,
        )


def _redact_url(url: str) -> str:
    """로그용 URL — query·fragment(1회용 ``token``)를 떼어 낸다."""
    return urlparse(url)._replace(query="", fragment="").geturl()


# 재생 끝 mark 를 보내기 전에 새 오디오가 이만큼 안 들어와야 한다(조각 TTS 대비).
PLAYOUT_QUIET_S = 0.3


class MediaWebSocket:
    """통화 하나의 미디어 스트림.

    인증은 서버가 준 URL 의 1회용 ``token`` 하나다. ``api_key`` 는 호환을 위해 받기만 하고
    쓰지 않는다 — 예전엔 ``Authorization: Bearer`` 로 실었지만 서버는 그 헤더를 읽은 적이
    없고, 서버가 ``ws://`` 를 주던 동안엔 계정 API 키가 평문 첫 요청에 실려 나갔다(clawops#1250).
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        on_audio: Callable[[bytes, int], Awaitable[None]],
        on_start: Callable[[dict[str, Any]], Awaitable[None]],
        on_stop: Callable[[], Awaitable[None]],
        on_dtmf: Callable[[str], Awaitable[None]] | None = None,
        expected_media_profile: MediaProfile | None = None,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._expected_media_profile = (
            MediaProfile(expected_media_profile) if expected_media_profile is not None else None
        )
        self._format_validated = asyncio.Event()
        if self._expected_media_profile is None:
            self._format_validated.set()
        self._url = url
        self._on_audio = on_audio
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_dtmf = on_dtmf
        self._on_event = on_event
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._send_task: asyncio.Task[None] | None = None
        self._mark_waiters: dict[str, asyncio.Event] = {}
        self._last_audio_at = 0.0

    async def connect(self) -> None:
        if self._expected_media_profile is not None:
            self._format_validated.clear()
        _warn_if_plaintext(self._url)
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(
            self._url,
            heartbeat=MEDIA_WS_HEARTBEAT_S,
        )
        log.info("Media WS connected: %s", _redact_url(self._url))
        self._send_task = asyncio.create_task(self._send_loop())

        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    event = data.get("event")

                    if event == "start":
                        info = parse_start_event(data)
                        if self._expected_media_profile is not None:
                            fmt = data["start"].get("mediaFormat", {})
                            self._expected_media_profile.validate_start({
                                **info,
                                "encoding": fmt.get("encoding", "audio/x-mulaw"),
                                "channels": fmt.get("channels", 1),
                            })
                            self._format_validated.set()
                        await self._on_start(info)
                    elif event == "media":
                        if not self._format_validated.is_set():
                            raise ValueError("Received media before format validation")
                        parsed = parse_media_event(data)
                        await self._on_audio(parsed["audio"], parsed["timestamp"])
                    elif event == "dtmf":
                        if self._on_dtmf:
                            await self._on_dtmf(parse_dtmf_event(data)["digit"])
                    elif event == "mark":
                        mark_name = data.get("mark", {}).get("name", "")
                        if mark_name in self._mark_waiters:
                            self._mark_waiters.pop(mark_name).set()
                    elif event == "stop":
                        await self._on_stop()
                        break
                    elif self._on_event is not None:
                        await self._dispatch_event(data)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
        finally:
            await self.close()

    async def _dispatch_event(self, data: dict[str, Any]) -> None:
        """위에서 처리하지 않은 다운링크 메시지를 넘긴다. 콜백 오류가 오디오 수신을 끊지 않게 삼킨다."""
        try:
            await self._on_event(data)
        except Exception:  # noqa: BLE001
            log.exception("Media WS on_event 처리 실패: %s", data.get("event"))

    async def send_audio(self, audio: bytes) -> None:
        self._last_audio_at = time.monotonic()
        self._audio_queue.put_nowait(audio)

    async def send_clear(self) -> None:
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps({"event": "clear"}))

    async def send_mark(self, name: str) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps({
                "event": "mark",
                "mark": {"name": name},
            }))

    async def send_dtmf(self, digit: str) -> None:
        """단일 DTMF digit을 전송한다."""
        msg = build_dtmf_message(digit)
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps(msg))
        else:
            log.warning(f"DTMF send skipped (WS not connected): {digit}")

    @property
    def is_connected(self) -> bool:
        """WebSocket이 연결되어 있는지 확인한다."""
        return self._ws is not None and not self._ws.closed

    async def flush(self) -> None:
        """Wait for all queued audio to be sent."""
        while not self._audio_queue.empty() and self.is_connected:
            await asyncio.sleep(0.005)

    async def wait_for_mark(self, name: str, timeout: float = 5.0) -> None:
        """Wait for a named mark to be echoed back by the server."""
        if not self.is_connected:
            return
        evt = asyncio.Event()
        self._mark_waiters[name] = evt
        try:
            await asyncio.wait_for(evt.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # 마크가 안 왔을 뿐 — 정상 반환한다(호출자에게 raise 하지 않는다).
            pass
        finally:
            # timeout·cancel·정상 완료 모두 waiter 를 회수한다.
            # (정상 echo 경로에서는 이미 pop 됐으므로 pop(name, None) 은 no-op.)
            self._mark_waiters.pop(name, None)

    async def wait_played_out(self, timeout: float) -> bool:
        """지금까지 보낸 오디오가 발신자에게 다 재생될 때까지 기다린다. 재생 끝 echo 를 받았으면 True.

        서버는 mark 앞의 오디오를 RTP 로 다 내보낸 뒤 mark 를 돌려주고, ``clear`` 때는 남은
        mark 를 즉시 돌려준다(Twilio Media Streams 와 같은 의미). 그래서 순서가 둘 다 중요하다.
        - 오디오는 송신 큐를 거치고 mark 는 바로 나가므로, 큐를 먼저 비워야 mark 가 오디오를
          앞지르지 않는다.
        - 재생할 게 없으면 echo 가 곧장 오므로, waiter 를 **보내기 전에** 걸어 둬야 놓치지 않는다
          (``send_mark`` → ``wait_for_mark`` 순서는 그 사이에 온 echo 를 흘린다).
        """
        if not self.is_connected:
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # 오디오가 조각조각 이어서 들어오는 중이면(LiveKit 처럼 TTS 를 만들면서 흘려 보내는 경로)
        # 큐가 잠깐 빈 틈에 mark 를 보내 안내 앞부분만 재생된 시점을 끝으로 볼 수 있다. 새 오디오가
        # PLAYOUT_QUIET_S 동안 안 들어올 때까지 기다린 뒤 보낸다.
        while True:
            await self.flush()
            quiet_for = time.monotonic() - self._last_audio_at
            if quiet_for >= PLAYOUT_QUIET_S or loop.time() >= deadline:
                break
            await asyncio.sleep(PLAYOUT_QUIET_S - quiet_for)
        name = f"playout-{uuid.uuid4().hex[:12]}"
        evt = asyncio.Event()
        self._mark_waiters[name] = evt
        try:
            await self.send_mark(name)
            await asyncio.wait_for(evt.wait(), timeout=max(0.0, deadline - loop.time()))
            return self.is_connected
        except asyncio.TimeoutError:
            return False
        finally:
            self._mark_waiters.pop(name, None)

    async def _send_loop(self) -> None:
        """오디오 청크를 플랫폼으로 전송 (pacing은 플랫폼이 처리)."""
        try:
            await self._format_validated.wait()
            while True:
                chunk = await self._audio_queue.get()
                if chunk is None:
                    break
                if self._ws and not self._ws.closed:
                    msg = build_media_response(chunk)
                    await self._ws.send_str(json.dumps(msg))
        except asyncio.CancelledError:
            pass

    async def graceful_close(self) -> None:
        """Drain queued audio, wait for it to play out, then close.

        The hangup handshake: flush the send queue, send a mark, wait for the
        server to echo it back (so trailing audio finishes playing), then close.
        Shared by ``ClawOpsAgent`` and mediaUrl-dispatched server runners so the
        hangup protocol lives in one place.
        """
        await self.flush()
        mark_name = f"hangup-{int(time.time() * 1000)}"
        await self.send_mark(mark_name)
        await self.wait_for_mark(mark_name, timeout=5.0)
        await self.close()

    async def close(self) -> None:
        # 기다리던 mark 는 이제 안 온다 — 상한까지 붙잡지 말고 깨운다.
        for evt in self._mark_waiters.values():
            evt.set()
        self._audio_queue.put_nowait(None)
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()
