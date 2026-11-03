# 빠른 시작

## 설치

```bash
# 기본 (OpenAI Realtime 모드)
pip install clawops[agent,openai]

# 파이프라인 모드
pip install clawops[agent,deepgram,elevenlabs,openai]    # OpenAI LLM
pip install clawops[agent,deepgram,elevenlabs,anthropic-llm]  # Anthropic LLM
pip install clawops[agent,deepgram,elevenlabs,gemini]    # Gemini LLM
pip install clawops[agent,deepgram,elevenlabs,ollama]        # Ollama (로컬)

# MCP 서버 지원 포함
pip install clawops[agent,mcp]

# 전체 설치
pip install clawops[agent-all]

# LiveKit Agents 실행 — docs/agent/livekit.md 참조
pip install 'clawops[livekit]'
```

## 환경변수

```bash
export CLAWOPS_API_KEY="sk_..."
export CLAWOPS_ACCOUNT_ID="AC..."
export OPENAI_API_KEY="sk-..."           # OpenAI Realtime / OpenAILLM
export ANTHROPIC_API_KEY="..."           # AnthropicLLM
export GOOGLE_API_KEY="..."              # Gemini Realtime / GeminiLLM (Google AI)
# 또는 Google Cloud Vertex AI 사용 시 (GOOGLE_API_KEY 불필요)
# export GOOGLE_GENAI_USE_VERTEXAI=true
# export GOOGLE_CLOUD_PROJECT="your-project-id"
# export GOOGLE_CLOUD_LOCATION="us-central1"
export MISTRAL_API_KEY="..."             # MistralLLM
export GROQ_API_KEY="..."               # GroqLLM
export PERPLEXITY_API_KEY="..."          # PerplexityLLM
export TOGETHER_API_KEY="..."            # TogetherLLM
export FIREWORKS_API_KEY="..."           # FireworksLLM
export DEEPSEEK_API_KEY="..."            # DeepSeekLLM
export XAI_API_KEY="..."                 # XaiLLM
export DEEPGRAM_API_KEY="..."            # Pipeline: DeepgramSTT
export ELEVENLABS_API_KEY="..."          # Pipeline: ElevenLabsTTS
```

## 최소 예제

```python
from clawops.agent import ClawOpsAgent, OpenAIRealtime
import asyncio

agent = ClawOpsAgent(
    from_="07012341234",
    session=OpenAIRealtime(
        system_prompt="친절한 고객센터 상담원입니다.",
    ),
)

asyncio.run(agent.serve())  # Ctrl+C로 종료
```

이것만으로 `07012341234` 번호로 걸려오는 전화를 AI가 처리합니다.

## 설정 옵션

```python
from clawops.agent import ClawOpsAgent, OpenAIRealtime

agent = ClawOpsAgent(
    # 필수
    from_="07012341234",
    session=OpenAIRealtime(
        system_prompt="상담원입니다.",
        voice="marin",                    # marin, ash, ballad, coral, sage, verse
        model="gpt-realtime-2",
        language="ko",
        turn_detection={"type": "semantic_vad", "eagerness": "medium"},
        greeting=True,
        transcription_prompt="재진, 초진, 예약 변경",  # 전사 어휘 힌트 (전사 텍스트에만 영향)
    ),

    # 인증 (환경변수 대체 가능)
    api_key="sk_...",
    account_id="AC...",

    # 녹음
    recording=True,
    recording_path="./recordings",

    # 오디오 게인 (AI 기준)
    # rx (receive) = AI가 수신하는 오디오 = caller가 말하는 소리 → STT/LLM이 듣는 음량
    # tx (transmit) = AI가 송신하는 오디오 = AI가 말하는 소리 → caller가 듣는 음량
    # 값: 1.0 = 원본 그대로 (기본), 0 = 완전 무음, 2.0 = 2배 증폭, 0.5 = 절반으로 감쇄
    rx_gain=1.0,
    tx_gain=1.0,
)
```

### Gemini Realtime 사용 시

```python
from clawops.agent import ClawOpsAgent, GeminiRealtime

agent = ClawOpsAgent(
    from_="07012341234",
    session=GeminiRealtime(
        system_prompt="상담원입니다.",
        voice="Kore",
        language="ko",
    ),
)
```

> **Note:** 기본 모델이 `gemini-3.1-flash-live-preview`로 업데이트되었습니다. 이전 `gemini-2.5-flash-native-audio-preview-12-2025` 모델은 더 이상 지원되지 않습니다.

#### VAD (Voice Activity Detection) 설정

Gemini Live API의 음성 감지 세부 설정을 `realtime_input_config`로 전달할 수 있습니다.
구조는 [google-genai SDK의 `RealtimeInputConfig`](https://ai.google.dev/gemini-api/docs/live-api/capabilities#configure-automatic-vad)를 그대로 따릅니다.

```python
from clawops.agent import ClawOpsAgent, GeminiRealtime

agent = ClawOpsAgent(
    from_="07012341234",
    session=GeminiRealtime(
        system_prompt="상담원입니다.",
        voice="Kore",
        language="ko",
        realtime_input_config={
            "automatic_activity_detection": {
                "start_of_speech_sensitivity": "START_SENSITIVITY_HIGH",
                "end_of_speech_sensitivity": "END_SENSITIVITY_HIGH",
                "prefix_padding_ms": 20,
                "silence_duration_ms": 100,
            },
            "activity_handling": "NO_INTERRUPTION",
        },
    ),
)
```

| 파라미터 | 타입 | 설명 |
| --- | --- | --- |
| `realtime_input_config` | `dict` | Gemini VAD 설정. `automatic_activity_detection`, `activity_handling`, `turn_coverage` 등을 포함. |

#### 입력 음성 전사 설정

발신자 음성 전사에 언어 힌트와 고유 명사 목록을 `input_audio_transcription` 으로 넘길 수 있습니다.
구조는 [google-genai SDK의 `AudioTranscriptionConfig`](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/live-api/best-practices#transcription-language-code)를 그대로 따릅니다.

```python
session=GeminiRealtime(
    system_prompt="상담원입니다.",
    model="gemini-3.8-live",
    input_audio_transcription={
        "language_codes": ["ko-KR"],
        "custom_vocabulary": ["클로옵스", "ClawOps"],
    },
)
```

| 파라미터 | 타입 | 설명 |
| --- | --- | --- |
| `input_audio_transcription` | `dict` | 입력 음성 전사 설정. 지정하지 않으면 빈 설정(`{}`)을 보냅니다. |

| 키 | 설명 |
| --- | --- |
| `language_codes` | BCP-47 언어 코드 목록. 여러 개를 넣을 수 있습니다. |
| `custom_vocabulary` | 전사가 우선 인식할 용어 목록. 최대 1,000개이며, 넘으면 세션을 열 때 거절됩니다. |

`gemini-3.8-live`·`gemini-3.8-live-extended-thinking`·`gemini-3.1-flash-live-preview` 모두 두 키를 받습니다(2026-10-20 확인).
google-genai 2.13.0 이상이 필요합니다.

#### 모델과 thinking 설정

`model` 에는 Gemini Live API 모델명을 그대로 넣습니다. 새 모델이 나와도 SDK 업데이트 없이 이름만 바꾸면 됩니다.

```python
session=GeminiRealtime(
    system_prompt="상담원입니다.",
    model="gemini-3.8-live",
)
```

`gemini-3.8-live-extended-thinking` 처럼 생각 수준을 요구하는 모델은 `thinking_config` 로 넘깁니다.
구조는 [google-genai SDK의 `ThinkingConfig`](https://ai.google.dev/gemini-api/docs/thinking)를 그대로 따릅니다.

```python
session=GeminiRealtime(
    system_prompt="상담원입니다.",
    model="gemini-3.8-live-extended-thinking",
    thinking_config={"thinking_level": "LOW"},
)
```

| 파라미터 | 타입 | 설명 |
| --- | --- | --- |
| `thinking_config` | `dict` | Gemini thinking 설정. `thinking_level`, `thinking_budget` 등을 포함. 지정하지 않으면 보내지 않습니다. |

받는 값은 모델마다 다릅니다(2026-09-29 확인).

| 모델 | `thinking_config` |
| --- | --- |
| `gemini-3.8-live-extended-thinking` | `thinking_level` 필수 — `LOW`·`MEDIUM`·`HIGH` (`MINIMAL` 은 거절) |
| `gemini-3.8-live` | `thinking_level` 은 거절, `thinking_budget` 만 받음. 보통은 지정하지 않습니다 |
| `gemini-3.1-flash-live-preview` | `thinking_level`·`thinking_budget` 모두 받음 |

모델이 받지 않는 값을 넣으면 세션을 열 때 Gemini 가 거절 사유와 함께 실패합니다.
생각 수준을 높일수록 응답 전 대기가 길어지므로, 전화 상담에서는 `LOW` 부터 시작하는 것을 권장합니다.

#### 함수 호출 behavior 와 proactive audio (3.8 Live)

Gemini 3.8 Live 는 함수 호출 기본값이 비동기(`NON_BLOCKING`)입니다. 비동기 모드에서는 도구 결과를
기다리는 동안 모델이 계속 말하므로, 전화에서는 결과가 나올 때까지 모델이 기다리는 동기(`BLOCKING`)가
맞습니다. SDK 는 모델별 기본값을 함수 선언에 자동으로 싣고, `tool_behavior` 로 덮어쓸 수 있습니다.

```python
session=GeminiRealtime(
    system_prompt="상담원입니다.",
    model="gemini-3.8-live",
    tool_behavior="BLOCKING",   # 생략하면 3.8-live 는 BLOCKING 이 기본
)
```

| 파라미터 | 타입 | 설명 |
| --- | --- | --- |
| `tool_behavior` | `"BLOCKING" \| "NON_BLOCKING"` | 모든 함수 선언(커스텀·MCP·내장)에 실리는 `behavior`. 생략하면 모델별 기본값. |
| `proactive_audio` | `bool` | `proactivity.proactive_audio`. 지정하지 않으면 보내지 않습니다. |
| `api_version` | `str` | google-genai `http_options.api_version`. 기본 `v1beta`. |

모델별 동작(2026-10-10, 모델 페이지·실측 기준):

| 모델 | `behavior` | proactive audio |
| --- | --- | --- |
| `gemini-3.8-live` | 서버 기본값 NON_BLOCKING. SDK 기본값은 **BLOCKING** 명시 | 영구 ON. `False` 는 거절 |
| `gemini-3.8-live-extended-thinking` | **NON_BLOCKING 만** 받음(BLOCKING 은 하드 에러). SDK 기본값 NON_BLOCKING | 영구 ON. `False` 는 거절 |
| `gemini-3.1-flash-live-preview` | 동기만. SDK 는 보내지 않음 | 미지원 |

`gemini-3.8-live-extended-thinking` 은 비동기 도구 실행만 가능해, 도구 결과를 기다리는 동안 모델이
말할 수 있습니다. 도구를 쓰는 전화 에이전트에는 `gemini-3.8-live` 를 권장합니다.

타이밍 문제를 보고할 때는 `CLAWOPS_GEMINI_DEBUG=1` 로 띄워 `[gemini-trace]` 로그를 함께 보내 주세요.

#### 도구별 비동기 실행 (`behavior`·`scheduling`)

도구마다 모델이 결과를 기다릴지(`BLOCKING`) 기다리지 않고 대화를 이어 갈지(`NON_BLOCKING`) 정할 수 있습니다.
결과가 다음 문장인 조회는 BLOCKING, 저장·기록처럼 말할 게 없는 작업은 NON_BLOCKING 이 맞습니다.

```python
@agent.tool(behavior="NON_BLOCKING", scheduling="SILENT")
async def save_note(text: str):
    """통화 중 메모를 저장합니다."""
    await crm.save(text)           # 모델은 기다리지 않는다. None 을 돌려주면 결과를 말하지 않는다(SILENT).

@agent.tool(behavior="NON_BLOCKING", scheduling="INTERRUPT")
async def check_stock(sku: str) -> str:
    """재고를 확인합니다. 결과가 나오면 바로 알려 줍니다."""
    return await inventory.check(sku)

@agent.tool                        # 생략 = 세션 기본값(3.8-live 는 BLOCKING)
async def lookup_order(order_id: str) -> str:
    """주문을 조회합니다."""
    return await orders.get(order_id)
```

| 값 | 의미 |
| --- | --- |
| `behavior="BLOCKING"` | 결과를 받을 때까지 모델이 기다립니다. 그동안 `hold_audio` 가 재생됩니다. |
| `behavior="NON_BLOCKING"` | 도구가 도는 동안 모델이 계속 대화합니다. 결과는 끝나는 즉시 보내고, hold audio 는 틀지 않습니다. 서버가 호출을 취소하면(끼어들기 등) 진행 중인 도구를 취소합니다. |
| `scheduling="SILENT"` | 결과를 말하지 않고 기억만 합니다. 핸들러가 `None` 을 돌려주면 기본값입니다. |
| `scheduling="WHEN_IDLE"` | 지금 하던 말을 마친 뒤 전달합니다(서버 기본값). |
| `scheduling="INTERRUPT"` | 하던 말을 끊고 바로 전달합니다. |

세션 전체 기본값은 `GeminiRealtime(tool_behavior=..., tool_response_scheduling=...)` 이고 도구별 값이 우선합니다.
내장 도구(`hang_up`·`collect_dtmf`·`send_dtmf`·`transfer_call`)는 통화 제어라 항상 BLOCKING 입니다.
`gemini-3.8-live-extended-thinking` 은 NON_BLOCKING 만 받고 `scheduling` 을 거절하므로, 이 모델에서는 모든 도구가
NON_BLOCKING 이 되고 `scheduling` 은 보내지 않습니다. `scheduling` 은 OpenAI 등 다른 세션에서는 무시됩니다.

#### 긴 통화와 세션 수명

Gemini Live 는 WebSocket 연결이 약 10분에서 끝나고(끝나기 전 `go_away`), 압축을 켜지 않은 오디오 세션은
15분에서 끝납니다. SDK 는 접속 때마다 `session_resumption` 핸들을 보내 두고, 연결이 끝나면 그 핸들로 새 연결을
열어 대화를 이어 갑니다(기본 3회 시도). 재접속 구간에는 몇 초의 공백이 생길 수 있습니다.

15분을 넘는 통화라면 컨텍스트 압축을 켜야 세션 자체가 끝나지 않습니다.

```python
session=GeminiRealtime(
    system_prompt="상담원입니다.",
    model="gemini-3.8-live",
    context_window_compression={"sliding_window": {}},
)
```

재접속이 끝내 실패하면 통화에 `session_lost` 이벤트를 낸 뒤 통화를 끊습니다. 로그를 남기거나 안내를 하려면
`call_start` 에서 핸들러를 거세요.

```python
@agent.on("call_start")
async def on_start(call):
    async def on_lost(call, reason: str):
        log.error("Gemini 세션 유실: %s", reason)
    call.on("session_lost", on_lost)
```

| 파라미터 | 타입 | 설명 |
| --- | --- | --- |
| `context_window_compression` | `dict` | Gemini `ContextWindowCompressionConfig`. 긴 통화에 `{"sliding_window": {}}` 권장. 지정하지 않으면 보내지 않습니다. |
| `session_resumption` | `bool` | 재접속 사용 여부. 기본 `True`. |
| `reconnect_max_attempts` | `int` | 재접속 시도 상한. 기본 3. 다 실패하면 `session_lost` 뒤 통화를 끊습니다. |

### 음성 옵션

| 음성     | 특징                         |
| -------- | ---------------------------- |
| `marin`  | 기본값, 자연스러운 여성 음성 |
| `ash`    | 자연스러운 남성 음성         |
| `ballad` | 부드러운 남성 음성           |
| `coral`  | 밝은 여성 음성               |
| `sage`   | 차분한 여성 음성             |
| `verse`  | 중성적 음성                  |

## 발신 (Outbound Call)

```python
from clawops.agent import ClawOpsAgent, OpenAIRealtime
import asyncio

async def main():
    agent = ClawOpsAgent(
        from_="07012345678",
        session=OpenAIRealtime(
            system_prompt="예약 확인 도우미입니다.",
        ),
    )

    # 발신만 하는 경우 — 자동으로 connect() 수행
    session = await agent.call("01012345678", timeout=60)
    print(session.call_id)     # 즉시 사용 가능
    print(session.direction)   # "outbound"
    await session.wait()       # 통화가 끝날 때까지 대기 (무응답이어도 리턴)
    await agent.disconnect()

    # 수신도 같이 하는 경우 (혼합 모드)
    await agent.connect()
    session = await agent.call("01012345678")
    # agent는 인바운드 수신도 계속 처리

asyncio.run(main())
```

| 파라미터            | 타입  | 기본값 | 설명                                                                                                  |
| ------------------- | ----- | ------ | ----------------------------------------------------------------------------------------------------- |
| `to`                | `str` | 필수   | 수신 전화번호                                                                                          |
| `timeout`           | `int` | `60`   | 상대방이 받지 않을 때 발신을 취소하기까지의 대기 시간 (초)                                             |
| `machine_detection` | `str` | 없음   | 음성사서함 감지(AMD). `"Enable"`=결과만 통보(통화 계속), `"Hangup"`=사서함 감지 시 자동 종료. 결과는 통화 조회의 `answered_by` 와 status callback 의 `AnsweredBy` 로 확인 |

### 발신 결과 확인하기

`await session.wait()` 는 **상대가 받지 않아도** 발신이 취소되는 시점에 리턴합니다.
따라서 리턴했다는 사실만으로는 통화가 성사됐는지 알 수 없고, `ended_status` 로 확인해야 합니다.

```python
session = await agent.call("01012345678", timeout=60)
await session.wait()

if session.ended_status == "completed":
    print("통화 완료")
else:
    print(f"통화 미연결: {session.ended_status}")   # no-answer / busy / rejected / ...
```

| `ended_status` | 의미 |
| --- | --- |
| `completed` | 상대가 받았고 통화가 정상 종료됨 |
| `no-answer` | 벨은 울렸으나 받지 않음 (`timeout` 초과로 발신 취소) |
| `busy` | 통화중 |
| `rejected` | 상대가 거절 |
| `canceled` | 상대가 받기 전에 발신 측이 취소 |
| `failed` | 시스템/네트워크 오류 |

**`completed` 만이 실제로 연결된 통화를 의미합니다.**

여러 건을 발신하거나 결과를 콜백으로 받고 싶다면 `call_failed` 이벤트를 쓰세요.
연결되지 못하고 끝난 통화에서만 발화됩니다.

```python
@agent.on("call_failed")
async def on_failed(call, reason):
    print(f"{call.to_number} 미연결: {reason}")
```

통화 한 건은 반드시 `call_start`+`call_end`(연결됨) 또는 `call_failed`(미연결) 중 한쪽으로 끝납니다.
자세한 내용은 [이벤트 & CallSession](events.md) 을 참고하세요.

#### 나중에 다시 조회하기

프로세스가 이미 종료됐거나 다른 서버에서 결과를 확인해야 한다면 통화 조회 API 를 쓰세요.

```python
from clawops import AsyncClawOps

async with AsyncClawOps() as client:          # CLAWOPS_API_KEY / CLAWOPS_ACCOUNT_ID 사용
    call = await client.calls.get(session.call_id)
    print(call.status)                        # ended_status 와 같은 값
```

더 자세한 진행 내역(통신망 응답, 벨 울림 여부 등)은 대시보드의 통화 상세 화면이나
통화 이벤트 조회 API(`GET /v1/accounts/{accountId}/calls/{callId}/events`)에서 볼 수 있습니다.

### 번호 하나당 프로세스 하나

플랫폼은 **발신번호 1개당 Agent 연결(control WebSocket) 1개**만 유지합니다.
같은 번호로 새 연결이 들어오면 기존 연결은 즉시 끊깁니다.

```
[프로세스 A] agent.serve()          ← 수신 대기 중
[프로세스 B] agent.call("010...")   ← 같은 번호로 연결 → A 가 끊기고 B 가 소유권 획득
```

이 상태에서 발신하면 통화가 응답된 직후 아래 오류로 끊길 수 있습니다.

```
AGENT_SESSION_INIT_FAILED
SDK 의 _active_sessions 에 callId 가 없습니다.
```

수신과 발신을 함께 하려면 **하나의 프로세스에서** `await agent.connect()` 후 `agent.call()` 을 호출하세요
(위 "혼합 모드" 예제). 발신 스크립트를 여러 번 실행할 때는 이전 프로세스가 완전히 종료됐는지 확인하세요.

## 에러 처리

```python
from clawops.agent import AgentConnectionError, AgentError, ClawOpsAgent, OpenAIRealtime
import asyncio

agent = ClawOpsAgent(
    from_="07012341234",
    session=OpenAIRealtime(
        system_prompt="상담원입니다.",
    ),
)

async def main():
    try:
        await agent.serve()
    except AgentConnectionError as e:
        print(f"서버 연결 실패: {e}")
    except AgentError as e:
        print(f"에이전트 에러: {e}")

asyncio.run(main())
```

| 에러                   | 설명                            |
| ---------------------- | ------------------------------- |
| `AgentError`           | Agent 관련 에러의 베이스 클래스 |
| `AgentConnectionError` | WebSocket 연결 실패             |

> 연결 에러(SSL 인증서, WebSocket 등)가 계속된다면 **[트러블슈팅](troubleshooting.md)** 가이드를 참고하세요.

## 디버그 로깅

```python
import logging
logging.getLogger("clawops.agent").setLevel(logging.DEBUG)
```
