# 이벤트 & CallSession

## 이벤트 핸들러

`@agent.on()` 데코레이터로 통화 이벤트를 수신합니다.

```python
@agent.on("call_start")
async def on_call_start(call):
    print(f"통화 시작: {call.from_number} -> {call.to_number}")
    print(f"통화 ID: {call.call_id}")

@agent.on("call_end")
async def on_call_end(call):
    print(f"통화 종료: {call.call_id} (총 {call.duration:.1f}초)")

@agent.on("transcript")
async def on_transcript(call, role, text):
    print(f"[{role}] {text}")
    # role: "user" (고객 음성 인식) 또는 "assistant" (AI 응답)

@agent.on("call_failed")
async def on_failed(call, reason):
    print(f"통화 미연결: {reason}")
    # reason: "no-answer" / "busy" / "rejected" / "canceled" / "failed"
```

### 이벤트 목록

| 이벤트 | 파라미터 | 설명 |
|--------|----------|------|
| `call_start` | `(call)` | 통화 시작 — **상대가 받은 뒤** 미디어 세션이 열릴 때 |
| `call_end` | `(call)` | 통화 종료 — `call_start` 가 발화된 통화만 |
| `call_failed` | `(call, reason)` | 통화가 **연결되지 못하고** 종료됨. `reason` 은 종료 사유 |
| `transcript` | `(call, role, text)` | 음성 텍스트 생성 |
| `transcript_delta` | `(call, role, text)` | 말하는 도중의 텍스트. `text` 는 그 발화의 지금까지 누적 문장입니다. `LiveKitSession` 만 보냅니다 |
| `dtmf` | `(call, digit)` | DTMF 키 입력 수신 |

`call_start`/`call_end` 는 통화가 **응답된 뒤** 열리는 미디어 세션에 묶여 있습니다. 상대가 받지 않았거나
(무응답) 통화중·거절이면 이 두 이벤트는 발화되지 않고, 대신 **`call_failed` 가 발화**됩니다.
즉 발신 한 건은 반드시 `call_start`+`call_end` 또는 `call_failed` 중 한쪽으로 끝납니다.

| 결과 | 발화되는 이벤트 |
|------|-----------------|
| 상대가 받고 통화 종료 | `call_start` → `call_end` |
| 무응답 · 통화중 · 거절 · 취소 | `call_failed` |
| 연결됐으나 시스템 오류로 종료 | `call_start` → `call_end` + `call_failed`(`reason="failed"`) |

> `reason` 값은 `call.ended_status` 와 동일합니다. 자세한 의미는
> [발신 결과 확인하기](quickstart.md#발신-결과-확인하기) 를 참고하세요.

## 수신 통화 거절·바로 넘기기 — `@agent.on_incoming`

수신 통화를 **받기 전에** 보고 거절하거나, AI 없이 다른 번호로 바로 넘길 수 있습니다. 거절한 통화는 응답 전에 끝나므로 요금이 붙지 않고,
세션·미디어가 열리지 않으며 `call_start`/`call_end` 등 이벤트도 발화되지 않습니다.

```python
from clawops.agent import IncomingCall

@agent.on_incoming
async def screen(call: IncomingCall):
    if call.caller_id_restricted:      # 번호 표시제한 통화
        await call.reject()            # 거절(SIP 403)
    elif call.from_number in blocklist:
        await call.reject(reason="busy")  # 통화중으로 거절(SIP 486)
    # reject() 를 부르지 않고 끝나면 평소대로 받습니다.
```

데코레이터 대신 `agent.on_incoming(screen)` 으로 등록해도 됩니다. 훅은 하나만 둘 수 있고, 다시 등록하면
앞의 것을 바꿉니다. `async def` 와 일반 함수 둘 다 됩니다.

### AI 없이 바로 넘기기 — `call.transfer()`

지정한 발신자만 AI 가 받고, 나머지는 AI 안내 없이 상담 번호로 바로 연결할 때 씁니다.

```python
AI_TARGETS = {"01012345678", "01087654321"}

@agent.on_incoming
async def screen(call: IncomingCall):
    if call.from_number not in AI_TARGETS:
        await call.transfer("0212345678", caller_id_mode="original")
```

- AI 세션을 열지 않습니다. 첫 인사도, 모델 연결도 없고 발신자는 연결음을 듣습니다.
- 통화가 연결되면 곧바로 전환합니다. `call_start`/`call_end` 등 이벤트는 발화되지 않습니다.
- 인자는 [`call.transfer()`](#callsession) 와 같습니다. 단 AI 가 이어받을 수 없으므로 `mode="blind"`·
  `after_transfer="terminate"` 로 고정됩니다. 대상이 받지 않으면 `failure_message` 를 들려주고 끊습니다.
- 번호 오류 등으로 전환을 시작하지 못하면 통화를 끊습니다.
- 받은 통화이므로 전환 전 통화 시간과 전환 통화 요금이 붙습니다.

### `IncomingCall`

`CallSession` 이 아닙니다 — 받기 전이라 오디오를 보낼 수 없습니다.

| 속성·메서드 | 타입 | 설명 |
|-------------|------|------|
| `call_id` | `str` | 통화 ID |
| `from_number` | `str` | 발신 번호. 번호 표시제한 통화도 번호는 그대로 옵니다 |
| `to_number` | `str` | 수신 번호(이 에이전트의 번호) |
| `caller_id_restricted` | `bool` | 발신자가 번호 표시제한을 걸고 건 통화면 `True` |
| `reject(reason="rejected")` | | 거절합니다. `reason` 은 `"rejected"`(기본, SIP 403) 또는 `"busy"`(SIP 486). 그 밖의 값은 `ValueError` |
| `transfer(to, ...)` | | AI 없이 받아서 `to` 로 넘깁니다. 위 절 참고 |

### 결정 규칙

| 훅의 결과 | 처리 |
|-----------|------|
| `reject()` 호출 | 그 즉시 거절합니다. 훅이 뒤에 더 일해도 기다리지 않습니다 |
| `transfer()` 호출 | 그 즉시 받아서 넘깁니다. 훅이 뒤에 더 일해도 기다리지 않습니다 |
| `reject()` 없이 끝남 | 받습니다 (훅이 없을 때와 같음) |
| 예외 발생 | 경고 로그를 남기고 **받습니다** |
| 5초 안에 결정 없음 | 경고 로그를 남기고 **받습니다**. 그 뒤의 `reject()` 는 경고만 남기고 무시됩니다 |
| 훅 등록 안 함 | 지금까지와 똑같이 받습니다 |

선별 코드에 문제가 생겨도 전화를 놓치지 않도록, 결정하지 못한 통화는 받는 쪽으로 처리합니다.
`reject()`·`transfer()` 는 먼저 부른 것만 효과가 있습니다. 훅이 도는 사이 발신자가 끊으면 아무 일도 하지 않습니다.

## CallSession

개별 통화의 상태를 관리합니다. 이벤트 핸들러의 `call` 파라미터로 전달됩니다.

### 속성

| 속성 | 타입 | 설명 |
|------|------|------|
| `call_id` | `str` | 통화 ID |
| `from_number` | `str` | 발신 번호 |
| `to_number` | `str` | 수신 번호 |
| `account_id` | `str` | 계정 ID |
| `direction` | `str` | `"inbound"` 또는 `"outbound"` |
| `caller_id_restricted` | `bool` | 수신 통화에서 발신자가 번호 표시제한(`*23#` 등)을 걸고 건 통화면 `True`. 번호는 `from_number` 에 그대로 전달됩니다. 통신사가 표시제한 여부를 보내지 않은 통화와 발신 통화는 `False` |
| `status` | `str` | 현재 상태. 아래 표 참고 |
| `ended_status` | `str \| None` | 종료 사유. 통화가 끝나기 전에는 `None` |
| `ended_duration` | `int \| None` | **서버가 확정한 통화 시간(초).** 아래 설명 참고 |
| `start_time` | `datetime` | 통화 시작 시간 |
| `duration` | `float` | SDK 가 로컬 시계로 재는 경과 시간 (초). 통화 중에도 읽힙니다 |
| `metadata` | `dict` | 사용자 정의 메타데이터 |

> `ended_duration` 은 서버의 종료 프레임이 도착할 때 채워집니다. 이 프레임은 미디어 스트림이
> 닫힌 뒤에 오므로 `call_end` 핸들러가 도는 시점에는 아직 `None` 일 수 있습니다 — 종료 직후
> 곧바로 읽지 말고, 짧게 뒤에 읽거나 통화 기록 적재 시점에 읽으세요.

#### `duration` 과 `ended_duration`

| | 의미 | 언제 읽나 |
|:--|:--|:--|
| `duration` | SDK 가 로컬 시계로 재는 경과 시간 | 통화 중에도 읽힙니다 |
| `ended_duration` | **서버가 확정한 통화 시간** | 통화가 끝난 뒤 |

기록·정산에는 `ended_duration` 을 쓰세요. `duration` 은 세션이 붙기 전후의 오차를 포함합니다.

**보통은 `call_end` 핸들러 안에서 바로 읽을 수 있습니다.** 서버는 미디어 스트림을 먼저 닫고
정리를 마친 뒤에 종료 정보를 보내므로, SDK 가 그 값을 짧게 기다렸다가 `call_end` 를 발화합니다.

> **전환(`transfer_call`)으로 끝나는 통화는 예외입니다.** 전환이 시작되면 AI 의 미디어 세션이
> 먼저 끝나므로 `call_end` 가 그 시점에 발화하는데, **통화 자체는 담당자와 계속 이어지고 있습니다.**
> 그래서 그 순간에는 전체 통화 시간이 아직 정해지지 않았고 `ended_duration` 은 `None` 입니다.
> 전환 구간의 길이는 `transfer_call()` 의 반환값(`duration`)으로 받으시고, 전체 통화 시간이
> 필요하면 통화 종료 webhook(`statusCallback`) 이나 통화 조회 API 를 쓰세요.

#### `status` / `ended_status` 값

통화가 진행되는 동안 `status` 는 아래처럼 바뀝니다.

| 값 | 시점 |
|----|------|
| `queued` | 발신(outbound) 세션 생성 직후 |
| `ringing` | 수신(inbound) 세션 생성 직후 / 발신은 통신망이 벨 신호를 올렸을 때 |
| `in-progress` | 발신 통화가 응답되어 미디어 세션이 열릴 때 |

통화가 끝나면 `status` 와 `ended_status` 모두 아래 종료 사유 중 하나가 됩니다.

| 값 | 의미 |
|----|------|
| `completed` | 상대가 받았고 통화가 정상 종료됨 |
| `no-answer` | 벨은 울렸으나 받지 않음 (`timeout` 초과로 발신 취소) |
| `busy` | 통화중 |
| `rejected` | 상대가 거절 |
| `canceled` | 상대가 받기 전에 발신 측이 취소 |
| `failed` | 시스템/네트워크 오류 |

**`completed` 만이 실제로 연결된 통화를 의미합니다.**

```python
session = await agent.call("01012345678")
await session.wait()
if session.ended_status != "completed":
    print(f"통화 미연결: {session.ended_status}")
```

### 메서드

```python
@agent.on("call_start")
async def on_start(call):
    call.metadata["customer_id"] = "CUST_123"

    await call.send_audio(ulaw_bytes)    # 오디오 전송 (G.711 μ-law, 8kHz)
    await call.clear_audio()             # 오디오 큐 초기화 (인터럽트 시)
    await call.hangup()                  # 통화 종료
    await call.transfer("01012345678")   # 다른 번호로 통화 전환
    await call.wait()                    # 통화 종료까지 대기 (아웃바운드 시 유용)
```

> **`send_audio()` 는 G.711 μ-law (8kHz, mono) 를 받습니다. PCM16 이 아닙니다.**
> 전송 계층이 받은 바이트를 μ-law 로 그대로 해석하므로, PCM16 을 넣으면 예외 없이
> **잡음으로 재생**됩니다. 직접 오디오를 만들어 넣을 때는 먼저 μ-law 로 변환하세요.
> Pipeline 모드를 쓰면 SDK 가 변환을 대신하므로 이 규격을 다룰 필요가 없습니다.

> `await call.wait()`는 통화가 종료될 때까지 대기합니다. 주로 아웃바운드 단건 발신 시 통화가 끝나기를 기다리는 데 사용합니다.
> 상대가 받지 않아도(무응답) 발신 취소 시점에 리턴하므로, **성사 여부는 리턴 후 `call.ended_status` 로 확인**하세요.
