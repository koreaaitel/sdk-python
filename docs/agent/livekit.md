# LiveKit Agents 실행

[LiveKit Agents](https://docs.livekit.io/agents/) 로 작성한 음성 에이전트를 **LiveKit
서버도 SIP 도 없이** 실제 ClawOps 전화번호로 실행합니다.

## 설치

```bash
pip install "koreaaitel[livekit]"
pip install 'livekit-plugins-openai' 'livekit-plugins-cartesia'   # 쓰는 플러그인만
```

Python 3.10+ 필요, Alpine(musl) 미지원.

## 예제

```python
import asyncio

from livekit.agents import Agent, AgentSession
from livekit.plugins import cartesia, openai

from clawops.agent import ClawOpsAgent
from clawops.agent.livekit import LiveKitSession


async def create(call):          # 통화당 1회 호출
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(modalities=["text"]),
        tts=cartesia.TTS(model="sonic-3.5", language="ko"),
    )
    return session, Agent(instructions="당신은 친절한 예약 상담원입니다.")


agent = ClawOpsAgent(from_="07012341234", session=LiveKitSession(create))
asyncio.run(agent.serve())       # 착신 대기. 발신은 await agent.call(to=...)
```

`create` 가 `(AgentSession, Agent)` 를 반환하는 것이 전부입니다. `Agent` 서브클래스,
[`@function_tool`](https://docs.livekit.io/agents/build/tools/), `on_enter`, handoff 등
LiveKit 코드는 그대로 씁니다. 전체 스크립트: [`examples/livekit_agent.py`](../../examples/livekit_agent.py).

## 무엇이 되고 안 되나

ClawOps 는 room 없는 transport 라, LiveKit 기능 중 room/서버에 묶인 것은 안 됩니다.

| | |
|---|---|
| ✅ **그대로** | `Agent` 서브클래스(`on_enter`/`@function_tool`/handoff/`llm_node`·`tts_node` 오버라이드) · `AgentSession(...)` · `RunContext`(`userdata`) · `generate_reply`/`say`/`@session.on(...)` · `silero.VAD` · `inference.TurnDetector()`(한국어 지원) · Cartesia·Deepgram 등 HTTP 플러그인 |
| 🔧 **한 줄 수정** | `session.start(room=...)` → `room=` 을 빼세요. ClawOps 가 `start()` 를 대신 부릅니다 |
| ⚠️ **LiveKit 키 필요** | `inference.STT/LLM/TTS` 는 LiveKit Cloud 를 호출합니다. 없이 쓰려면 `openai.LLM(...)`/`cartesia.TTS(...)` 로 provider 직접 지정 |
| ❌ **불가** | `noise_cancellation.BVC()` · `ctx.api.*` · `ctx.wait_for_participant()` · `RoomOptions`/`AudioInputOptions` · LiveKit SIP 기반 `WarmTransferTask`/`AMD` · 아바타 |

## 알아둘 점

- **통화 제어 도구** `hang_up`/`collect_dtmf`/`send_dtmf`/`transfer_call` 이 자동
  주입됩니다. `ClawOpsAgent(builtin_tools=...)` 로 켜고 끄며, 유저 도구와 이름이
  겹치면 내장 쪽을 뺍니다. `transfer_call` 은 ClawOps 전환(PSTN/SIP)을 씁니다.
- **`modalities=["text"]` 인데 `tts` 가 없으면** 소리가 안 납니다. ClawOps 는 이 경우
  시작 시점에 `ValueError` 를 던집니다.
- **녹음·telemetry** 는 LiveKit `record=` 가 아니라 ClawOps 자체 기능을 씁니다.
- **동시통화** — `session_factory=lambda: LiveKitSession(create)` 로 넘기면 통화마다
  세션을 새로 만들어 격리합니다. `session=LiveKitSession(create)` 는 통화 사이에 같은
  객체를 공유하므로 동시통화 1건까지입니다. 팩토리 모드에서는 HTTP 플러그인용
  `http_context` 도 통화별로 열립니다.

## 명시적 미디어 프로필 (서버 연동용)

기존 연결은 계속 G.711 μ-law 8kHz mono입니다. SDK는 서버가 지원을 확인한 내부
managed 연결을 위해 `MediaProfile.PCM_S16LE_16000`도 처리합니다. 이 SDK 변경만으로
운영 전화나 `ClawOpsAgent`의 미디어 형식이 바뀌지는 않습니다. 서버 지원 없이
16kHz를 선택하면 안 됩니다. 일반 `ClawOpsAgent`, pipeline, realtime 세션은 기존
8kHz 경로를 유지합니다.

```python
from clawops.agent.media import MediaProfile
from clawops.agent.livekit import LiveKitSession
from clawops.agent import MediaWebSocket

profile = MediaProfile.PCM_S16LE_16000
session = LiveKitSession(create, media_profile=profile)
# MediaWebSocket(..., expected_media_profile=profile)
```

프로필은 **prewarm 전에** 정하고 같은 값을 `LiveKitSession`과 `MediaWebSocket`의
`expected_media_profile`에 전달해야 합니다. PCM 프로필의 WS 바이트는 signed
16-bit **little-endian**, 16,000Hz, mono이며 20ms는 640바이트입니다. RTP L16의
network byte order와 다릅니다. `start.mediaFormat`의 encoding은
`audio/pcm;encoding=signed-integer;bits=16;endianness=little`, sampleRate는 16000,
channels는 1이어야 합니다. 기본 프로필은 encoding `audio/x-mulaw`, 8000Hz,
20ms 160바이트입니다.

`expected_media_profile`을 지정하면 서버의 `start` 형식 확인 전에는 큐의 오디오를
전송하지 않습니다. 불일치하면 연결을 닫아, 이미 prewarm한 PCM을 구 서버에서
μ-law로 재생하는 일을 막습니다. 프로필을 바꾸려면 세션을 새로 만드세요. 재생 시간,
pause/resume 되감기, mark 완료와 tail padding도 프로필의 바이트율을 사용합니다.
SDK 릴리스 후 runner와 call-engine이 이 계약을 함께 구현·검증한 뒤 활성화해야 합니다.
