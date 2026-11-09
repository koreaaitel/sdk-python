"""LiveKit AudioInput/AudioOutput <-> ClawOps Media WS 브리지.

LiveKit 의 AgentSession 은 room 없이도 동작한다 — `session.input.audio` /
`session.output.audio` 에 커스텀 구현을 꽂으면 된다. 이 모듈이 그 구현이다.

기본 wire format 은 G.711 μ-law 8kHz, 160바이트(20ms)이다. 명시적으로 선택한
PCM16 LE 16kHz 프로필은 640바이트(20ms)이다. AudioOutput 이 프로필의 샘플률을
선언하므로 프레임워크가 TTS 출력을 맞춘다. 프로필은 prewarm 전에 고정한다.

참조 구현: livekit `cli/tcp_console.py:TcpAudioOutput` — 같은 문제(비-WebRTC
transport 를 AgentSession 에 물리기)를 푸는 LiveKit 자신의 코드다.

⚠️ AudioOutput 계약 3가지 — 어기면 조용히 깨진다:

1. 이 싱크는 반드시 `TranscriptSynchronizer` 로 감싸야 한다. 감싸지 않으면
   barge-in 시 `synchronized_transcript` 가 None 이라 LLM 컨텍스트에 **말하지 않은
   전체 텍스트**가 기록된다. `_session.py` 가 이 래핑을 책임진다.
2. 첫 프레임에서 `on_playback_started()` 를 반드시 호출한다. 이걸 빼면
   `first_frame_fut` 이 resolve 되지 않아 assistant 메시지가 히스토리에서
   **통째로 사라진다**.
3. `on_playback_finished()` 는 세그먼트당 정확히 한 번, `flush()` 가 띄운
   태스크에서만 호출한다. `clear_buffer()` 에서 호출하면 재생 회계가 깨진다.

## 일시정지(pause/resume) — 오탐 끼어들기에서 안내를 되살린다

LiveKit 은 발신자 음성이 감지되면 안내를 **일시정지**하고, 정해진 시간 안에 실제 말(전사·턴
커밋)이 없으면 **재개**한다(`resume_false_interruption`, 기본 2초). 이 싱크가 `can_pause`
를 못 내면 LiveKit 은 그 기능을 통째로 끄고 매 통화 경고만 남긴다 — 회선 잡음·기침 한 번에
안내가 영영 끊긴다(CASE-2026-000751).

WebRTC 와 달리 우리 오디오는 이미 엔진(call-engine)의 페이서 큐에 세그먼트째 들어가 있어
"보내기를 멈추는 것" 만으로는 소리가 안 멈춘다. 그래서:

- `pause()` = 지금까지 들린 위치를 추정해 기억하고 엔진 큐를 `clear` 한다(즉시 정적).
  이 세그먼트의 μ-law 는 전부 로컬에 남겨 둔다.
- `resume()` = 기억한 위치(살짝 앞당겨서)부터 남은 바이트를 다시 보낸다. 추정이 늦은 쪽으로
  틀리면 음절이 사라지고 이른 쪽이면 살짝 반복된다 — 반복이 낫다(:data:`_RESUME_REWIND_S`).
- ⚠️ 엔진은 `clear` 를 받으면 **대기 중인 mark 를 즉시 되돌려 준다**(call-handler.js). 그걸
  "재생 완료" 로 읽으면 세그먼트가 일시정지 중에 닫혀 버려 재개할 것이 없어진다. 그래서 mark
  에 세대(:attr:`_mark_gen`)를 박고, pause 가 세대를 올려 옛 mark 를 무효로 만든다.
  `_await_playout` 은 무효 mark 를 보면 재개를 기다렸다가 새 mark 를 다시 건다.
- 들린 위치는 첫 바이트 송신 시각부터의 경과로 추정한다(끊김 시 `playback_position` 과 같은
  근거). 엔진 프리롤(수십 ms)만큼 늦게 들리므로 경과는 실제보다 조금 크다 — 되감기가 그 오차도
  덮는다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any

from livekit import rtc
from livekit.agents.voice import io

from .._audio import pcm16_to_ulaw, ulaw_to_pcm16
from ..media import MediaProfile

log = logging.getLogger("clawops.agent.livekit")

SAMPLE_RATE = 8000
"""전화망 wire rate. AudioOutput 이 이걸 선언하면 프레임워크가 여기 맞춰 리샘플한다."""

FRAME_BYTES = 160
"""μ-law 20ms = 160바이트."""

ULAW_SILENCE = b"\xff"
"""μ-law 무음."""

_TAIL_PAD = os.environ.get("CLAWOPS_TAIL_PAD") == "1"
"""세그먼트 끝 자투리를 무음으로 채워 보낼지 (기본: 안 채움).

⛔ 킬스위치다. 엔진이 자투리를 못 받는 구버전으로 롤백될 때만 켠다 —
   `RTP_SEND_QUEUE_MODE=legacy` 를 켤 거면 **이것도 함께(또는 먼저)** 켜야 한다.
   구 엔진 + 비패딩 SDK 조합은 세그먼트마다 짧은 RTP 패킷을 만든다."""

_MARK_TIMEOUT_MARGIN = 10.0
"""mark 대기 timeout = 밀어넣은 오디오 길이 + 이 여유."""

_PLAYOUT_GATE_MAX_WAIT_S = 900.0
"""재생 게이트(:meth:`ClawOpsAudioOutput.set_playout_gate`)가 열리기를 기다리는 상한(초).

관리형 러너의 응답 mark 대기 상한(``ANSWER_MARK_TIMEOUT_S``)과 같다. 그 안에 안 열리면 종전처럼
그때부터 오디오 길이 + 여유만큼만 더 기다린다."""

_RESUME_REWIND_S = 0.12
"""재개 지점을 추정 위치보다 이만큼 앞당긴다(음절 유실보다 짧은 반복이 낫다)."""

_SENTINEL = object()


def _swap_samples(pcm: bytes) -> bytes:
    swapped = bytearray(len(pcm))
    swapped[0::2], swapped[1::2] = pcm[1::2], pcm[0::2]
    return bytes(swapped)


class ClawOpsAudioInput(io.AudioInput):
    """선택된 wire 오디오 -> rtc.AudioFrame 스트림.

    `LiveKitSession.feed_audio()` 가 `push_audio()` 로 밀어넣는다.
    """

    def __init__(self, *, media_profile: MediaProfile = MediaProfile.MULAW_8000) -> None:
        super().__init__(label="ClawOps")
        self._profile = MediaProfile(media_profile)
        self._queue: asyncio.Queue[Any] = asyncio.Queue()

    def push_audio(self, audio: bytes) -> None:
        """Decode the selected wire contract to native-endian LiveKit PCM16."""
        pcm = ulaw_to_pcm16(audio) if self._profile is MediaProfile.MULAW_8000 else audio
        if not pcm:
            return
        if len(pcm) % 2:
            raise ValueError("PCM16 audio must contain complete samples")
        if sys.byteorder != "little":
            pcm = _swap_samples(pcm)
        frame = rtc.AudioFrame(
            data=pcm,
            sample_rate=self._profile.sample_rate,
            num_channels=1,
            samples_per_channel=len(pcm) // 2,
        )
        self._queue.put_nowait(frame)

    def push_ulaw(self, ulaw: bytes) -> None:
        """Legacy μ-law entry point; never reinterpret it as PCM."""
        if self._profile is not MediaProfile.MULAW_8000:
            raise ValueError("push_ulaw requires the mulaw_8000 profile")
        self.push_audio(ulaw)

    def end_input(self) -> None:
        """스트림 종료 — `__anext__` 가 StopAsyncIteration 을 내게 한다."""
        self._queue.put_nowait(_SENTINEL)

    async def __anext__(self) -> rtc.AudioFrame:
        item = await self._queue.get()
        if item is _SENTINEL:
            raise StopAsyncIteration
        return item  # type: ignore[no-any-return]


class ClawOpsAudioOutput(io.AudioOutput):
    """AgentSession 출력 -> 선택된 wire 프레임 -> CallSession.send_audio().

    재생 완료(playout) 판정은 Media WS 의 `mark` 로 한다 — 플랫폼이 큐에 쌓인
    오디오를 다 내보낸 뒤 mark 를 에코해준다. prewarm 중(`_BufferingCall`)에는
    media_ws 가 없으므로 즉시 완료로 본다(실제로 아직 아무것도 재생되지 않지만,
    버퍼는 attach 시 전부 flush 되므로 "전부 재생됨"이 맞다).

    ``pause=True`` 면 LiveKit 에 일시정지 능력을 알린다(모듈 docstring 「일시정지」).
    기본은 False — 켜면 LiveKit 이 오탐 끼어들기 되살리기를 실제로 수행하므로 끼어들기
    동작이 달라진다. 켜는 쪽(러너·에이전트)이 결정한다.
    """

    def __init__(
        self, call: Any, *, pause: bool = False,
        media_profile: MediaProfile = MediaProfile.MULAW_8000,
    ) -> None:
        self._profile = MediaProfile(media_profile)
        super().__init__(
            label="ClawOps",
            next_in_chain=None,
            sample_rate=self._profile.sample_rate,
            capabilities=io.AudioOutputCapabilities(pause=pause),
        )
        self._call = call
        # ── 세그먼트 상태(flush 태스크가 닫을 때 리셋) ──
        self._pushed_duration: float = 0.0  # 캡처된 오디오 총 길이(초)
        self._seg = bytearray()  # 이 세그먼트의 wire 오디오 전체 — 재개 재전송용
        self._sent = 0  # _seg 중 엔진으로 보낸 바이트 수
        self._segment_closed = False  # flush() 가 불렸다(자투리까지 내보낸다)
        # ── 재생 위치 추정 기준점: 기준 바이트 + 그 시각 ──
        self._playing = False
        self._origin_bytes = 0
        self._origin_time = 0.0
        # ── 일시정지(세그먼트를 넘어 유지된다 — LiveKit 은 싱크 수준 상태로 본다) ──
        self._paused = False
        self._pause_pos = 0  # 일시정지 시점의 추정 재생 위치(바이트)
        self._resume_from = 0  # 재개 시 다시 보내기 시작할 바이트
        self._mark_gen = 0  # pause 마다 올린다 — 옛 mark 무효화
        self._resumed_ev = asyncio.Event()
        self._send_lock = asyncio.Lock()
        # ── 수명 ──
        self._flush_task: asyncio.Task[None] | None = None
        self._interrupted_ev = asyncio.Event()
        self._attached_ev = asyncio.Event()
        self._bg_tasks: set[asyncio.Task[None]] = set()
        # 재생 게이트 — set 되기 전에는 엔진이 오디오를 내보내지 않는다(set_playout_gate).
        self._playout_gate: asyncio.Event | None = None

    def set_playout_gate(self, gate: asyncio.Event | None) -> None:
        """엔진이 보관 중인 오디오를 **언제 실제로 내보내기 시작하는지** 알리는 이벤트를 단다.

        관리형 발신은 상대가 받기 전에 인사를 만들어 두고, 엔진이 받은 뒤 그 오디오(와 그 뒤의
        mark)를 함께 푼다. 그 사이 mark 대기에 「오디오 길이 + 여유」 상한을 그대로 걸면 벨이 길 때
        mark 가 오기 전에 시간이 다 돼 인사를 「다 말했다」고 판단한다 — 「첫 인사는 끝까지 말하기」가
        인사가 들리기도 전에 풀린다(CASE-2026-004501). 게이트가 열리기 전에는 상한을 세지 않는다.

        ``None`` 이거나 이미 set 이면 종전과 같다. mark 가 게이트보다 먼저 오면(보관하지 않는 엔진)
        그대로 재생 완료로 본다.
        """
        self._playout_gate = gate

    def set_call(self, call: Any) -> None:
        """attach() 시 실제 CallSession 으로 교체 (prewarm -> 실제 통화).

        prewarm 중 버퍼링된 오디오는 지금부터 실제로 재생되기 시작하므로
        경과 시간 기준점을 여기로 옮긴다 — 안 그러면 링 구간까지 "재생됐다"고
        계산돼서 barge-in 시 절단 위치가 뒤로 밀린다.
        """
        self._call = call
        if self._sent:
            self._playing = True
            self._origin_bytes = 0
            self._origin_time = time.monotonic()
        self._attached_ev.set()

    # ── AudioOutput 구현 ────────────────────────────────────────

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        if self._profile is MediaProfile.PCM_S16LE_16000 and (
            frame.sample_rate != self._profile.sample_rate or frame.num_channels != 1
        ):
            raise ValueError("Audio frame must match the selected sample rate and mono channels")
        pcm = bytes(frame.data)
        if self._profile is MediaProfile.PCM_S16LE_16000 and len(pcm) != frame.samples_per_channel * 2:
            raise ValueError("PCM16 audio must contain complete samples")
        if sys.byteorder != "little":
            pcm = _swap_samples(pcm)
        await super().capture_frame(frame)

        # 이전 세그먼트의 flush 가 아직 진행 중이면 기다린다 (TcpAudioOutput 과 동일).
        if self._flush_task and not self._flush_task.done():
            log.warning("capture_frame called while previous flush is in progress")
            await self._flush_task

        if not self._pushed_duration:
            # ⚠️ 계약 2: 이걸 빼면 assistant 메시지가 히스토리에서 사라진다.
            self.on_playback_started(created_at=time.time())

        self._pushed_duration += frame.duration
        self._seg += pcm16_to_ulaw(pcm) if self._profile is MediaProfile.MULAW_8000 else pcm
        await self._pump()

    def flush(self) -> None:
        super().flush()
        # 캡처된 세그먼트가 없으면 닫을 것도 없다.
        if self._pending_playback_count <= 0:
            self._seg = bytearray()
            self._sent = 0
            return
        self._segment_closed = True
        # ⚠️ 이전 flush 태스크를 취소하지 않는다 — 취소하면 그 세그먼트의
        #    on_playback_finished 가 유실돼 프레임워크 wait_for_playout 이 영구 대기한다.
        #    대신 _flush_and_wait 이 이전 태스크를 먼저 await 해 직렬화한다.
        prev = self._flush_task
        self._flush_task = asyncio.create_task(self._flush_and_wait(prev))

    def clear_buffer(self) -> None:
        # ⚠️ 계약 3: 여기서 on_playback_finished 를 부르지 않는다.
        #    _flush_and_wait 이 _interrupted_ev 를 보고 한 번만 쏜다.
        if self._pushed_duration:
            self._interrupted_ev.set()
        # prewarm 중 _BufferingCall.clear_audio 는 버퍼를 비운다(실제 통화면 큐 flush).
        self._spawn(self._call.clear_audio())

    def pause(self) -> None:
        """재생을 멈춘다(엔진 큐 clear). 들린 위치를 기억해 두고 resume 에서 그 앞부터 다시 보낸다."""
        super().pause()
        if self._paused:
            return
        pos = self._estimate_played_bytes()  # ⚠️ _paused 를 세우기 전에 — 세운 뒤엔 멈춘 자리를 돌려준다
        self._paused = True
        self._mark_gen += 1  # 이 이후 엔진이 되돌려 주는 옛 mark 는 재생 완료가 아니다
        self._pause_pos = pos
        rewind = int(_RESUME_REWIND_S * self._profile.bytes_per_second)
        self._resume_from = max(0, pos - rewind) // self._profile.frame_bytes * self._profile.frame_bytes
        self._playing = False  # 재개 시 기준점을 새로 잡는다
        if self._sent:
            self._spawn(self._call.clear_audio())
        log.debug(
            "audio output paused",
            extra={"played_ms": pos * 1000 // self._profile.bytes_per_second, "sent_bytes": self._sent},
        )

    def resume(self) -> None:
        """멈춘 지점(살짝 앞)부터 남은 오디오를 다시 보낸다."""
        super().resume()
        if not self._paused:
            return
        self._paused = False
        self._sent = min(self._resume_from, len(self._seg))
        self._spawn(self._resend())

    def close(self) -> None:
        """세션 종료 시 남은 재생 태스크를 정리한다 (닫힌 통화 참조 방지)."""
        self._interrupted_ev.set()  # prewarm·일시정지 대기 중인 _flush_and_wait 을 깨운다
        self._resumed_ev.set()
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        for task in list(self._bg_tasks):
            task.cancel()

    # ── 내부 ────────────────────────────────────────────────────

    def _spawn(self, coro: Any) -> None:
        """sync 컨텍스트에서 async 호출 — GC 방지용으로 참조를 붙든다."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._on_bg_done)

    def _on_bg_done(self, task: asyncio.Task[Any]) -> None:
        self._bg_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.warning(f"background task failed: {task.exception()}")

    def _media_ws(self) -> Any | None:
        """연결된 Media WS. prewarm 중(_BufferingCall)에는 None."""
        ws = getattr(self._call, "_media_ws", None)
        if ws is None or not ws.is_connected:
            return None
        return ws

    def _estimate_played_bytes(self) -> int:
        """지금까지 상대에게 들렸을 바이트 수(추정). 일시정지 중엔 멈춘 자리."""
        if self._paused:
            return self._pause_pos
        if not self._playing:
            return 0
        elapsed = int((time.monotonic() - self._origin_time) * self._profile.bytes_per_second)
        played = max(0, min(self._sent, self._origin_bytes + elapsed))
        return played // self._profile.bytes_per_sample * self._profile.bytes_per_sample

    async def _pump(self) -> None:
        """_seg 의 아직 안 보낸 부분을 선택된 20ms 프레임으로 보낸다.

        세그먼트가 닫혔으면(flush 뒤) 자투리도 그대로 내보낸다 — 예전엔 여기서 무음으로
        채워 160바이트를 맞췄는데 그게 발화 한가운데 0~19ms 짜리 구멍을 만들고 있었다
        (실측 2026-08-19: 발화 중 갭이 전부 끝 위치 `mod 160 = 0`, 길이 160 미만).
        「뒤에 더 오는가」는 큐를 들고 있는 엔진만 안다. 엔진은 뒤따르는 mark 를 세그먼트
        끝 신호로 읽어 그 자리에서 채워 내보낸다.
        """
        async with self._send_lock:
            if self._paused:
                return
            self._sent = min(self._sent, len(self._seg))
            avail = len(self._seg) - self._sent
            if self._segment_closed:
                if avail and avail % self._profile.frame_bytes and _TAIL_PAD:
                    padding = self._profile.frame_bytes - avail % self._profile.frame_bytes
                    self._seg += self._profile.silence_byte * padding
                end = len(self._seg)
            else:
                end = self._sent + avail // self._profile.frame_bytes * self._profile.frame_bytes
            if end <= self._sent:
                return
            if not self._playing:
                self._playing = True
                self._origin_bytes = self._sent
                self._origin_time = time.monotonic()
            # 이 호출의 모든 청크는 한 통화로 보낸다 (attach 스왑 중 분할 방지).
            call = self._call
            off = self._sent
            while off < end:
                chunk = bytes(self._seg[off : min(off + self._profile.frame_bytes, end)])
                await call.send_audio(chunk)
                off += len(chunk)
            self._sent = end

    async def _resend(self) -> None:
        try:
            await self._pump()
        finally:
            self._resumed_ev.set()

    async def _wait_mark(self, ws: Any, name: str) -> None:
        """mark echo 를 기다린다. 재생 게이트가 닫혀 있으면 열린 뒤부터 상한을 센다(set_playout_gate)."""
        budget = self._pushed_duration + _MARK_TIMEOUT_MARGIN
        gate = self._playout_gate
        if gate is None or gate.is_set():
            await ws.wait_for_mark(name, timeout=budget)
            return
        # 받기 전 — 엔진이 오디오와 mark 를 보관 중이다. mark 대기는 지금 걸어 둔다(먼저 오면 그걸로 끝).
        mark_task = asyncio.ensure_future(ws.wait_for_mark(name, timeout=_PLAYOUT_GATE_MAX_WAIT_S + budget))
        gate_task = asyncio.ensure_future(gate.wait())
        try:
            done, _ = await asyncio.wait(
                {mark_task, gate_task},
                timeout=_PLAYOUT_GATE_MAX_WAIT_S,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if mark_task in done:
                return
            # 지금부터 실제로 들리기 시작한다 — 들린 위치 추정 기준점도 여기로(set_call 과 같은 이유).
            if gate_task in done and self._playing and not self._paused:
                self._origin_bytes = 0
                self._origin_time = time.monotonic()
            try:
                await asyncio.wait_for(asyncio.shield(mark_task), timeout=budget)
            except asyncio.TimeoutError:
                pass
        finally:
            for task in (mark_task, gate_task):
                if not task.done():
                    task.cancel()

    async def _race_interrupt(self, awaitable: Any) -> bool:
        """`awaitable` 과 barge-in 중 먼저 오는 걸 기다린다. 반환: awaitable 이 이겼는가.

        진 쪽은 취소한다 — wait_for_mark 은 취소돼도 waiter 를 회수한다(_media_ws.py).
        """
        main = asyncio.ensure_future(awaitable)
        interrupt = asyncio.create_task(self._interrupted_ev.wait())
        try:
            await asyncio.wait([main, interrupt], return_when=asyncio.FIRST_COMPLETED)
            won = bool(main.done())
            if won:
                # main 은 우리만 소유하며 아직 취소 전이므로 .exception() 이 안전하다.
                # 예외가 있으면 consume 해 "never retrieved" 경고를 막는다.
                main.exception()
            return won
        finally:
            main.cancel()
            interrupt.cancel()

    async def _flush_and_wait(self, prev: asyncio.Task[None] | None) -> None:
        # 이전 세그먼트의 flush 태스크를 먼저 끝낸다 (취소하지 않고 직렬화).
        if prev is not None and not prev.done():
            try:
                await prev
            except asyncio.CancelledError:
                # 우리 자신이 취소된 경우엔 전파해야 close() 가 실제로 멈춘다.
                # prev 가 취소된 경우엔 무시하고 우리 세그먼트를 처리한다.
                if asyncio.current_task().cancelling():  # type: ignore[union-attr]
                    raise
            except Exception as e:
                log.warning(f"previous flush segment failed: {e}")

        # prev 가 자기 세그먼트를 닫은 뒤 남은 미완료 세그먼트가 없으면 종료.
        if self._pending_playback_count <= 0:
            self._seg = bytearray()
            self._sent = 0
            self._segment_closed = False
            return

        interrupted = True
        try:
            # 남은 자투리를 **그대로** 내보낸다(일시정지 중이면 재개 때 _resend 가 보낸다).
            await self._pump()
            interrupted = await self._await_playout()
        except Exception:
            # WS 사망 등 전송 오류 — interrupted(=True) 로 처리하고 프레임워크로
            # 전파하지 않는다(전파하면 capture 루프가 깨진다). CancelledError 는
            # Exception 이 아니므로 여기 안 걸리고 그대로 전파되며 finally 가 emit 한다.
            log.warning("playout error, treating as interrupted", exc_info=True)
        finally:
            # ⚠️ 계약 3: 세그먼트당 정확히 한 번 — 취소되더라도 반드시 emit 한다.
            #    (안 그러면 segment count 가 어긋나 wait_for_playout 이 영구 대기한다.)
            if interrupted:
                played = min(self._estimate_played_bytes() / self._profile.bytes_per_second, self._pushed_duration)
            else:
                played = self._pushed_duration
            self.on_playback_finished(playback_position=played, interrupted=interrupted)
            self._pushed_duration = 0.0
            self._seg = bytearray()
            self._sent = 0
            self._segment_closed = False
            self._playing = False
            # 일시정지 상태 자체는 유지한다(LiveKit 이 resume 을 따로 부른다) — 위치만 리셋.
            self._pause_pos = 0
            self._resume_from = 0
            self._interrupted_ev.clear()

    async def _await_playout(self) -> bool:
        """재생이 끝나거나 barge-in 될 때까지 대기. 반환값: interrupted 여부."""
        ws = self._media_ws()
        if ws is None:
            # prewarm 중 — 아직 재생 주체가 없다. 여기서 바로 완료로 처리하면
            # attach 후 인사말이 실제로 나갈 때 barge-in 해도 컨텍스트가 안 잘린다
            # (모델이 인사말을 다 했다고 믿는다). attach 될 때까지 기다렸다가
            # 그때부터 진짜 fence 를 건다.
            if not await self._race_interrupt(self._attached_ev.wait()):
                return True  # attach 전에 끊겼다
            ws = self._media_ws()
            if ws is None:
                # attach 됐는데도 WS 가 없다 (이미 종료된 통화 등) — fence 불가.
                return self._interrupted_ev.is_set()

        while True:
            if self._paused:
                # 일시정지 중 — 재개(또는 끊김)를 기다린 뒤 다시 mark 를 건다.
                self._resumed_ev.clear()
                if self._paused and not await self._race_interrupt(self._resumed_ev.wait()):
                    return True
                continue

            gen = self._mark_gen
            mark_name = f"lk-{gen}-{int(time.monotonic() * 1e6)}"
            # send_mark 는 WS 로 즉시 나가지만 send_audio 는 로컬 큐에 쌓인다.
            # flush() 로 큐를 비우지 않으면 mark 가 오디오를 추월한다 (_graceful_hangup 과 동일).
            # 락은 재개 재전송(_resend)이 끝난 뒤에 mark 가 서게 한다.
            async with self._send_lock:
                await ws.flush()
                await ws.send_mark(mark_name)

            # mark echo(재생 완료)와 barge-in 중 먼저 오는 것을 기다린다.
            if not await self._race_interrupt(self._wait_mark(ws, mark_name)):
                return True
            if gen != self._mark_gen or self._paused:
                # pause 의 clear 가 되돌려 준 옛 mark — 재생 완료가 아니다. 재개 후 다시 건다.
                continue
            return False
