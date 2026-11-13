# 한국AI통신 Python SDK

[한국AI통신](https://koreaaitel.com)(Korea AI Telecom) API 의 공식 Python 라이브러리입니다.
한국AI통신은 AI 에이전트를 위한 통화 인프라를 만드는 기간통신사업자입니다.
문서는 [docs.koreaaitel.com](https://docs.koreaaitel.com) 에 있습니다.

[![PyPI version](https://img.shields.io/pypi/v/clawops.svg)](https://pypi.org/project/clawops/)
[![Python 3.9+](https://img.shields.io/pypi/pyversions/clawops.svg)](https://pypi.org/project/clawops/)

## 설치

```bash
# REST API SDK만 사용
pip install clawops

# AI Agent 포함
pip install clawops[agent]

# 특정 프로바이더 포함
pip install clawops[agent,openai,deepgram,elevenlabs,mcp]

# 전체 설치
pip install clawops[agent-all]
```

## AI Agent (음성 에이전트)

`ClawOpsAgent`를 사용하면 한 줄로 인바운드 전화를 AI로 처리할 수 있습니다. ngrok 없이 WebSocket 역방향 연결로 동작합니다.

```python
from clawops.agent import ClawOpsAgent, OpenAIRealtime
import asyncio

agent = ClawOpsAgent(
    from_="07012341234",
    session=OpenAIRealtime(
        system_prompt="친절한 상담원입니다. 고객의 질문에 답변해주세요.",
        voice="marin",
        language="ko",
    ),
)

@agent.tool
async def check_order(order_id: str) -> str:
    """주문 상태를 확인합니다."""
    return "배송 완료"

@agent.on("call_start")
async def on_start(call):
    print(f"통화 시작: {call.from_number} -> {call.to_number}")

asyncio.run(agent.serve())  # Ctrl+C로 종료
```

### Outbound 발신 Prewarm (낮은 첫 음성 latency)

outbound 통화에서 상대 응답 직후 첫 음성까지의 지연을 줄이기 위해, ClawOpsAgent 는
control WS 의 `call.outbound_ready` 이벤트 수신 즉시 LLM WebSocket 을 미리 연결하고
첫 audio delta 를 메모리에 누적한다 (prewarm + first-audio prebuffer). media WS 가 연결되면
누적된 chunk 를 flush 하여 사용자가 첫 음성을 빠르게 듣게 한다.

```python
agent = ClawOpsAgent(
    from_="07012341234",
    session=OpenAIRealtime(system_prompt="..."),
    prewarm_enabled=True,  # default True
)
```

비용/효과 검증 단계에서는 `prewarm_enabled=False` 로 비활성화 가능하다.
측정 스크립트: `scripts/measure_prewarm_cost.py` (PREWARM-T 로그 파싱).

**한계 / 비목표**

- **`session=` 은 동시 통화 1건까지** — 그 객체를 모든 통화가 공유하므로 두 번째 통화가
  첫 통화의 대화 이력과 오디오 경로를 덮어쓴다. 동시 통화가 있으면 `session_factory=` 를
  쓴다 (아래 "동시 통화" 참고).
- **Session 타입별 효과 차이** — Realtime (OpenAI / Gemini) 에서 LLM WS handshake +
  session.update 가 prewarm 으로 숨겨지므로 latency 절감 효과가 가장 크다. 반면
  `PipelineSession` 은 STT / LLM / TTS 가 lazy 연결되므로, prewarm 단계에서는 STT 루프 기동과
  greeting kickoff 정도만 선행되어 latency 절감 효과가 제한적이다.

### 동시 통화

한 프로세스가 통화 여러 건을 동시에 받으려면 `session` 대신 **`session_factory`** 를 준다.
ClawOps 가 통화마다 팩토리를 호출해 새 세션을 만들고, 대화 이력·오디오 큐·전송 대상이
그 통화 안에 갇힌다.

```python
agent = ClawOpsAgent(
    from_="07012341234",
    session_factory=lambda: OpenAIRealtime(system_prompt="..."),
)
```

`session=` 을 주면 예전과 똑같이 동작한다 — 모든 통화가 같은 객체를 공유하므로
**동시 통화 1건까지만 안전하다.** 두 번째 통화가 시작되면 원인을 지목하는 에러 로그가 남는다.

동시 통화 한도 자체는 계정 요금제가 정한다(Individual 1 · Business 10). SDK 쪽 격리와는
별개이므로, 회선을 여러 개 운영하더라도 프로세스를 나눌 필요는 없다.

### Call Transfer (통화 전환)

AI가 통화 중 다른 번호로 전환할 수 있습니다. Blind(즉시)와 Warm(안내 후) 모드를 지원합니다.

```python
from clawops.agent import ClawOpsAgent, OpenAIRealtime, BuiltinTool

agent = ClawOpsAgent(
    from_="07012341234",
    session=OpenAIRealtime(
        system_prompt="고객 문의를 처리하고, 필요하면 상담원에게 전환하세요.",
    ),
    builtin_tools=[BuiltinTool.HANG_UP, BuiltinTool.TRANSFER_CALL],
)

# 코드에서 직접 전환도 가능
@agent.on("call_start")
async def on_start(call):
    # 조건에 따라 즉시 전환
    if should_transfer:
        await call.transfer("01012345678", mode="warm", whisper="VIP 고객입니다.")
```

### MCP 서버 연동

MCP 서버를 연결하여 AI에게 외부 도구를 제공할 수 있습니다.

```bash
pip install clawops[mcp]
```

```python
from clawops.agent import ClawOpsAgent, OpenAIRealtime
from clawops.agent.mcp import MCPServerStdio, MCPServerHTTP
import asyncio

agent = ClawOpsAgent(
    from_="07012341234",
    session=OpenAIRealtime(
        system_prompt="상담원입니다.",
    ),
    mcp_servers=[
        MCPServerStdio("npx", args=["@modelcontextprotocol/server-google"], env={"GOOGLE_API_KEY": "..."}),
        MCPServerHTTP("https://my-mcp-server.com", headers={"Authorization": "Bearer token"}),
    ],
)

asyncio.run(agent.serve())  # Ctrl+C로 종료
```

MCP 서버는 전화가 올 때마다 자동으로 시작되고, 통화 종료 시 정리됩니다. MCP 서버가 제공하는 도구는 `@agent.tool`로 등록한 도구와 함께 세션에 자동 등록됩니다.

### 디버그 로깅

Agent의 내부 동작 (MCP 연결, 도구 등록, 도구 호출 등)을 확인하려면 로깅 레벨을 `DEBUG`로 설정하세요.

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# 또는 clawops.agent 로거만 DEBUG로:
logging.getLogger("clawops.agent").setLevel(logging.DEBUG)
```

### OpenTelemetry Tracing

통화 흐름, MCP 도구 호출, LLM 세션을 OpenTelemetry로 추적할 수 있습니다.

```bash
pip install clawops[tracing]
# + 원하는 exporter
pip install opentelemetry-sdk opentelemetry-exporter-otlp
```

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)

from clawops.agent.tracing import TracingConfig

agent = ClawOpsAgent(
    ...,
    tracing=TracingConfig(),
)
```

**Span 계층:**
- `call` → `mcp.connect` → `llm.session` → `tool.call` → `mcp.call_tool`

> 자세한 사용법은 **[Agent 문서](docs/agent/)** 를 참고하세요. (Tool, 이벤트, 통화 녹음, 파이프라인 모드, 커스텀 제공자, MCP 연동, Tracing 등)

## REST API 사용법

```python
from clawops import ClawOps

client = ClawOps(
    api_key="sk_...",          # 또는 CLAWOPS_API_KEY 환경변수 사용
    account_id="AC1a2b3c4d",   # 또는 CLAWOPS_ACCOUNT_ID 환경변수 사용
)
```

### 통화 (Calls)

```python
# 발신 전화 생성
call = client.calls.create(
    to="01012345678",
    from_="07052358010",
    url="https://my-app.com/twiml",
    status_callback="https://my-app.com/status",
    status_callback_event="initiated ringing answered completed",
)
print(call.call_id)

# 음성사서함 감지(AMD) — Enable=결과만 통보(통화 계속), Hangup=사서함이면 자동 종료
amd_call = client.calls.create(
    to="01012345678",
    from_="07052358010",
    url="https://my-app.com/twiml",
    machine_detection="Enable",
)
# 통화 종료 후 결과 확인: human(사람) / machine(자동응답기) / unknown(판정 불가)
print(client.calls.get(amd_call.call_id).answered_by)
# status_callback 을 설정했다면 completed 이벤트 payload 의 AnsweredBy 로도 통보됩니다.

# 통화 목록 조회 (페이지네이션)
page = client.calls.list(status="completed", page=0, page_size=20)
for call in page:
    print(call.call_id, call.status)

# 모든 통화를 자동으로 순회
for call in client.calls.list().auto_paging_iter():
    print(call.call_id)

# 특정 통화 조회
call = client.calls.get("CAabcdef1234567890")

# 연결 실패 사유 확인 — status="failed" 는 결번·망 오류·시스템 오류를 모두 포함하는
# 대분류라, 다시 걸어도 소용없는 번호를 가려내려면 hangup_cause 를 봅니다.
DO_NOT_RETRY = {"invalid_number", "number_changed", "incompatible_destination"}
if call.status != "completed":
    if call.hangup_cause in DO_NOT_RETRY:
        print(f"결번 — 목록에서 제외: {call.to}")        # hangup_cause_q850=1, sip_response_code=404
    elif call.hangup_source in ("app", "system"):
        print("ClawOps 측 오류 — 재시도")
    else:
        print(f"일시적 사유({call.hangup_cause}) — 나중에 재시도")

# 통화 종료
call = client.calls.update("CAabcdef1234567890", status="completed")

# 통화 전사 상태 조회 (completed 시 segments 까지 inline)
state = client.calls.get_transcript("CAabcdef1234567890")
if state.status == "completed":
    for seg in state.segments or []:
        print(f"[{seg.speaker}] {seg.text}")
elif state.status == "not_requested":
    # 조직 설정 off 거나 아직 요청 안 된 상태 — 명시 요청 (사용량 과금)
    client.calls.request_transcript("CAabcdef1234567890")

# 통화 요약 상태 조회 (completed 시 result_json 까지 inline)
summary = client.calls.get_summary("CAabcdef1234567890")
if summary.status == "completed":
    print(summary.result_json)  # {"coreSummary": ..., "decisions": [...], ...}
```

### 통화 녹음 (Recordings)

콘솔에서 들리는 것과 동일한 서버측 MixMonitor 원본(WAV PCM 16bit mono 8kHz)을 다운로드합니다. SDK 측 mix.wav 가 아닌 서버에서 합성된 파일이라 싱크/볼륨이 정상입니다.

```python
# call list 응답의 recording_url 필드로 녹음 유무 확인 가능
page = client.calls.list(page_size=10)
for call in page.data:
    if not call.recording_url:  # failed/no-answer 등은 None
        continue
    rec = client.recordings.download(call.call_id)
    with open(rec.filename or f"{call.call_id}.wav", "wb") as f:
        f.write(rec.data)
    print(rec.content_type, len(rec.data), "bytes")
```

녹음이 없는 통화(`recording_url is None`)에 호출하면 `NotFoundError(404)` 가 발생합니다.

```python
# 녹음 삭제 (멱등 — 이미 없어도 성공)
client.recordings.delete("CAabcdef1234567890")
```

### 전화번호 (Numbers)

```python
# 번호 발급 — 풀에서 자동 배정되며 어떤 번호가 나올지는 지정할 수 없다
number = client.numbers.create()
print(number.number, number.routing_type)  # 07012340001 webhook

# 회선 수 한도 안이면 문자 발신번호로도 자동 등록된다
print(number.sms_sender)  # registered=True sender_number_id='…'

# 번호 목록 조회 (페이지네이션 없음 — 보유한 번호가 전부 반환된다)
numbers = client.numbers.list()

# 발급 직후에는 webhook_url 이 비어 있어 걸려온 전화가 거절된다. 착신 라우팅을 지정한다.

# 매니지드 에이전트가 받도록
client.numbers.update("07012340001", routing_type="agent", agent_id="AG7c2f9b1e4a6d")

# 콜 플로우(ARS)가 받도록
client.numbers.update("07012340001", routing_type="callflow", call_flow_id="CF41b8e07d9c25")

# 내 서버의 VoiceML 이 받도록
client.numbers.update(
    "07012340001",
    routing_type="webhook",
    webhook_url="https://my-app.com/voice",
)

# 보유한 다른 번호로 착신전환 (같은 계정의 번호만 가능)
client.numbers.update("07012340001", routing_type="forward", forward_to="07012340002")

# 소프트폰 단말 착신 (sip_trunk 부가서비스 + 등록 단말 필요)
creds = client.sip_credentials.list(status="active")
client.numbers.update("07012340001", routing_type="softphone", sip_credential_id=creds[0].id)

# 외부 PBX 로 (sip_trunk 부가서비스 + 활성 라우트 1개 이상 필요)
endpoints = client.sip_endpoints.list(status="active")
client.numbers.update("07012340001", routing_type="sip", sip_endpoint_id=endpoints[0].id)

# 수신 통화 상태 통지
client.numbers.update(
    "07012340001",
    status_callback="https://my-app.com/call-status",
    status_callback_events="initiated ringing answered completed",
)

# 번호 반납 — 되돌릴 수 없고 같은 번호를 다시 받는다는 보장이 없다
client.numbers.delete("07012340001")
```

라우팅을 바꾸면 다른 라우팅 필드는 서버에서 자동으로 비워집니다. `agent` 에서 `webhook` 으로
되돌리면 `agent_id` 가 `None` 이 되므로, 다시 `agent` 로 돌아갈 때 `agent_id` 를 새로 지정해야
합니다.

### 문자 발신번호 (Sender Numbers)

문자는 등록해 승인된 발신번호로만 보낼 수 있습니다. 승인되지 않은 번호를 `from_` 으로 보내면 `400 from_not_registered` 입니다.

```python
from clawops import ConflictError

# 등록된 발신번호·유예 기간인 번호·회선 수 한도
res = client.sender_numbers.list()
print(res.sender_numbers, res.grace, res.limit)

# ClawOps에서 개통한 070번호·대표번호 등록 — 즉시 승인
try:
    client.sender_numbers.create(number="07052358010")
except ConflictError as e:
    # 이미 등록된 번호면 이미 발송에 쓸 수 있는 상태다
    if e.code != "sender_number_already_registered":
        raise

# 삭제 — API 로는 ClawOps에서 개통한 번호만
client.sender_numbers.delete(res.sender_numbers[0].id)
```

발신번호 범위를 지정한 API 키는 조회만 됩니다(등록·삭제는 `403 api_key_sender_numbers_restricted`).

### 메시지 (Messages)

```python
# SMS 발송
msg = client.messages.create(
    to="01012345678",
    from_="07052358010",
    body="안녕하세요",
)
print(msg.message_id)

# MMS 발송
msg = client.messages.create(
    to="01012345678",
    from_="07052358010",
    body="사진 첨부",
    type="mms",
    subject="제목",
)

# LMS (장문 문자) 발송
message = client.messages.create(
    to="01012345678",
    from_="07052358010",
    body="긴 내용의 메시지입니다...",
    type="lms",
    subject="알림",
)

# 메시지 목록 조회 (필터링)
page = client.messages.list(type="sms", status="sent", page=0, page_size=20)

# 번호로 필터 (하이픈 유무를 모두 매칭), 알림톡만 골라 보기
page = client.messages.list(number="07052358010")
page = client.messages.list(type="ata")
for msg in page:
    print(msg.message_id, msg.status)

# 모든 메시지를 자동으로 순회
for msg in client.messages.list().auto_paging_iter():
    print(msg.message_id)

# 특정 메시지 조회
msg = client.messages.get("MG0123456789abcdef")
```

### 카카오 알림톡 (Kakao)

알림톡은 **검수된 템플릿**으로만 나갑니다. 본문을 요청으로 쓰는 게 아니라, 승인된 템플릿에 변수를 채워 보냅니다.

```python
# 1. 연결된 채널 찾기
channels = client.kakao.channels.list(status="connected")
channel = channels.data[0]

# 2. 그 채널의 템플릿 중 발송 가능한 것 고르기
#    ⚠️ status("APPROVED")가 아니라 sendable 이 정본입니다 — 휴면 여부까지 반영된 값입니다.
templates = client.kakao.templates.list(channel_id=channel.id)
template = next(t for t in templates if t.sendable)
print(template.variables)      # ['#{고객명}'] — 이 목록을 전부 채워야 합니다

# 3. 발송
msg = client.messages.create(
    to="01012345678",
    from_="07052358010",
    kakao={
        "channel_id": channel.id,
        "template_id": template.id,
        "variables": {"고객명": "홍길동"},   # '고객명' 과 '#{고객명}' 둘 다 받습니다
    },
    fallback={"body": "주문이 접수되었습니다."},
)
print(msg.type, msg.body)      # ata 홍길동님, 주문이 접수되었습니다.
```

- **`body` 와 `kakao` 는 섞을 수 없습니다.** `create()` 가 오버로드로 갈려 있어 타입 단계에서 걸리고, 타입체커를 쓰지 않아도 요청을 보내기 전에 `TypeError` 로 거절합니다.
- **버튼·아이템 리스트·강조 문구는 템플릿에 검수된 대로** 나갑니다. 요청으로 바꿀 수 없고, 응답 `body` 에도 담기지 않습니다.
- **`fallback` 은 별도 메시지 1건입니다.** 알림톡이 실패하면(수신자가 카카오톡을 쓰지 않는 등) 이 문구가 문자로 나가고 **문자 단가로 따로 청구**됩니다. 생략하면 템플릿 본문을 그대로 문자로 보냅니다. 아예 보내지 않으려면 `fallback={"disabled": True}`.

#### 채널 연결

두 단계입니다. 서버는 그 사이 상태를 저장하지 않으므로(미완료 담당자 번호를 남기지 않기 위해) `search_id` 와 `phone_number` 를 두 번 모두 보냅니다.

```python
# 업종 카테고리는 열린 집합입니다 — 하드코딩하지 말고 이 응답을 선택지로 쓰세요.
categories = client.kakao.channel_categories()

# ① 담당자 휴대전화로 인증번호 발송 (응답에 인증번호는 없습니다 — 202)
req = client.kakao.channels.request_token(search_id="example", phone_number="010-1234-5678")
print(req.phone_number_masked, req.retry_after_seconds)

# ② 받은 인증번호로 연결 완료
channel = client.kakao.channels.connect(
    search_id=req.search_id,
    phone_number="010-1234-5678",
    category_code=categories.data[0].code,
    token="394812",
)
```

- **`connect` 는 멱등이지만, 타임아웃되면 다시 보내지 마세요.** 이미 연결에 성공했을 수 있습니다. `client.kakao.channels.retrieve(channel_id)` 로 확인하세요 — 이 조회는 몇 번을 불러도 안전합니다.
- **연결에 실패해도 인증번호는 소모됩니다** (`KAKAO_TOKEN_INVALID`·`KAKAO_CHANNEL_REJECTED` 둘 다). 원인을 해결한 뒤 새로 요청해야 합니다. 다만 `429`/`503` 은 연결이 **시도되지 않았다**는 뜻이라 인증번호가 아직 유효합니다.
- **`list()` 는 카카오 쪽 상태를 확인하지 않습니다.** 저장된 연결 정보를 그대로 돌려주므로 빠릅니다. 실제 상태 확인은 `retrieve()` 뿐이고, 그 호출이 `status` 를 갱신합니다.

```python
# 연결 해제 — ⚠️ 되돌릴 수 없고, 그 채널의 알림톡 템플릿도 함께 삭제됩니다.
#              템플릿은 카카오 검수를 다시 받아야 하므로 복구에 시간이 걸립니다.
client.kakao.channels.disconnect(channel.id)
```

### 카카오 브랜드 메시지 (Brand)

채널을 **추가한 친구**에게 나가는 광고성 메시지입니다. 등록한 템플릿으로 보냅니다.

```python
channels = client.kakao.channels.list(status="connected")
templates = client.kakao.brand_templates.list(channel_id=channels.data[0].id)

msg = client.messages.create(
    to="01012345678",
    from_="07052358010",
    brand={
        "channel_id": channels.data[0].id,
        "template_id": templates.data[0].id,
        "variables": {"고객명": "홍길동"},
    },
)
print(msg.type)  # 'bms'
```

⭐ **알림톡과 달리 검수가 없습니다** — `brand_templates.list()` 가 돌려준 템플릿은 전부 바로
쓸 수 있어 `sendable` 같은 칸이 없습니다. 알림톡 템플릿과는 **다른 표**라 `templates` 가 아닌
`brand_templates` 로 조회합니다.

알림톡과 갈리는 점 둘:

- **야간에 보낼 수 없습니다.** 오후 8시 50분 ~ 다음 날 오전 8시(KST)는
  `422 kakao_brand_night_blocked` 입니다. 접수 전에 막으므로 예약되지 않습니다.
- **대체발송이 없습니다.** 카카오톡을 쓰지 않는 수신자에게 문자로 대신 나가지 않습니다.
  `fallback` 을 함께 주면 타입 에러이고, 타입체커를 쓰지 않아도 `TypeError` 로 거절합니다.

`(광고)` 표기와 수신거부 안내는 **카카오가 붙이므로** 본문에 넣지 마십시오.

⚠️ 단가가 알림톡보다 훨씬 높고 **말풍선 유형에 따라 갈립니다** — 템플릿의 `chat_bubble_type`
이 그 축입니다. `content` 는 유형에 따라 `None` 일 수 있습니다(본문이 담기는 자리가 다릅니다).

#### 자유형 — 템플릿 없이 보내기

`template_id` 대신 `free` 에 말풍선을 실으면 **템플릿을 등록하지 않고** 보냅니다. 종료된
친구톡을 대신하는 방식입니다.

```python
client.messages.create(
    to="01012345678",
    from_="07052358010",
    brand={
        "channel_id": channel_id,
        "free": {
            "chatBubbleType": "TEXT",
            "content": "이번 주 신메뉴가 나왔어요.",
            "buttons": [{"name": "메뉴 보기", "linkType": "WL", "linkMobile": "https://example.com"}],
        },
    },
)
```

`template_id` 와 `free` 는 **정확히 하나만** 실을 수 있습니다 — 둘 다 주거나 둘 다 빼면
mypy 가 잡고, 타입을 쓰지 않으면 `TypeError` 입니다.

⛔ **자유형에는 변수를 쓸 수 없습니다.** 치환해 줄 템플릿이 없어 `#{이름}` 이 그대로 톡에
렌더되므로 서버가 막습니다. 값을 채우려면 템플릿을 등록해 `template_id` 로 보내십시오.

⚠️ `free` 안쪽은 **SDK 가 검사하지 않습니다.** 말풍선 규격표는 서버에 한 벌만 두는 것이
의도입니다 — SDK 가 사본을 들면 카카오가 칸을 늘린 날 SDK 가 조용히 깎습니다.

⚠️ 검수를 거치지 않으므로 **여기 실은 버튼 링크가 그대로 톡에 렌더됩니다.**

#### 자유형에 이미지 넣기

```python
image = client.kakao.brand_images.upload(
    file=open("banner.png", "rb").read(),
    filename="banner.png",
    bubble_type="WIDE",
)

client.messages.create(
    to="01012345678",
    from_="07052358010",
    brand={
        "channel_id": channel_id,
        "free": {"chatBubbleType": "WIDE", "content": "신메뉴가 나왔어요.", "imageId": image.id},
    },
)
```

**한 번 올린 이미지는 여러 발송에 재사용합니다.** ⚠️ 규격이 말풍선 유형마다 달라, 올릴 때 준
`bubble_type` 과 다른 유형에 쓰면 카카오가 발송 단계에서 거절합니다. 와이드리스트형의 작은
항목만 `slot="sub"` 를 씁니다.

`id` 를 잃었으면 `client.kakao.brand_images.list()` 로 되찾습니다.

### 멀티 계정 접근

```python
# 다른 계정의 리소스에 접근
other = client.accounts("AC_other_account_id")
other.calls.list()
other.numbers.list()
other.messages.list()
```

## 비동기 사용법

```python
from clawops import AsyncClawOps

# async context manager 사용
async with AsyncClawOps(api_key="sk_...", account_id="AC1a2b3c4d") as client:
    call = await client.calls.create(
        to="01012345678",
        from_="07052358010",
        url="https://my-app.com/twiml",
    )
    print(call.call_id)

    # 모든 리소스 메서드는 비동기 버전을 제공합니다
    page = await client.calls.list(status="completed")
    async for call in page.auto_paging_iter():
        print(call.call_id)

    # 메시지 발송
    msg = await client.messages.create(
        to="01012345678", from_="07052358010", body="안녕하세요",
    )
```

## 웹훅 서명 검증

```python
client.webhooks.verify(
    url="https://my-app.com/webhook",
    params={"CallId": "CA...", "CallStatus": "completed"},
    signature=request.headers["X-Signature"],
    signing_key="your_account_signing_key",
)
```

서명이 유효하지 않으면 `WebhookVerificationError`가 발생합니다.

## 에러 처리

```python
from clawops import ClawOps, BadRequestError, AuthenticationError, NotFoundError

client = ClawOps()

try:
    call = client.calls.create(to="01012345678", from_="07052358010", url="https://...")
except BadRequestError as e:
    print(f"잘못된 요청: {e.status_code} - {e.body}")
except AuthenticationError as e:
    print(f"유효하지 않은 API 키: {e.status_code}")
except NotFoundError as e:
    print(f"리소스를 찾을 수 없음: {e.status_code}")
```

모든 에러는 `ClawOpsError`를 상속합니다. HTTP 에러는 `status_code`, `code`, `response`, `body`, `request` 속성을 제공합니다.

#### 사유는 `code` 로 분기하세요

같은 상태 코드에 여러 사유가 몰립니다 — `422` 만 해도 수신거부·할당량 초과·템플릿 미승인이 섞입니다. 메시지 문구는 바뀌지만 `code` 는 계약입니다.

```python
from clawops import BadRequestError, UnprocessableEntityError

try:
    client.messages.create(to=..., from_=..., kakao={...})
except BadRequestError as e:
    if e.code == "kakao_variable_missing":
        ...   # 템플릿이 요구하는 변수를 덜 채웠다
    elif e.code == "kakao_variable_unknown":
        ...   # 템플릿에 없는 변수를 보냈다
except UnprocessableEntityError as e:
    if e.code == "recipient_blocked":
        ...   # 수신거부 명단에 있다 (발송 자체가 일어나지 않아 이력에도 남지 않는다)
    elif e.code == "quota_exceeded":
        ...
```

⚠️ 서버가 도메인마다 표기를 달리 씁니다 — 문자 도메인은 `snake_case`(`kakao_variable_missing`), 채널 연동은 `SCREAMING_CASE`(`KAKAO_TOKEN_INVALID`)입니다. SDK 는 실제 응답과 어긋나지 않도록 **섞인 채로** 노출합니다. 서버가 코드를 새로 만들어도 그대로 실립니다.

| 에러                       | 상태 코드 |
| -------------------------- | --------- |
| `BadRequestError`          | 400       |
| `AuthenticationError`      | 401       |
| `PermissionDeniedError`    | 403       |
| `NotFoundError`            | 404       |
| `ConflictError`            | 409       |
| `UnprocessableEntityError` | 422       |
| `InternalServerError`      | 500+      |
| `ServiceUnavailableError`  | 503       |

## 설정

### 재시도

기본적으로 `408`, `409`, `429`, `500+` 에러 시 지수 백오프로 최대 2회 재시도합니다.

```python
client = ClawOps(max_retries=5)

# 재시도 비활성화
client = ClawOps(max_retries=0)
```

### 타임아웃

기본 타임아웃은 600초 (연결 타임아웃 5초)입니다. 클라이언트 또는 요청 단위로 변경할 수 있습니다:

```python
# 클라이언트 단위
client = ClawOps(timeout=30.0)

# 요청 단위
call = client.calls.create(..., timeout=10.0)
```

### 커스텀 HTTP 클라이언트

프록시, 커스텀 인증서 등 고급 설정이 필요한 경우 `httpx.Client`를 직접 주입할 수 있습니다:

```python
import httpx

client = ClawOps(
    http_client=httpx.Client(proxies="http://proxy.example.com:8080"),
)
```

## 환경변수

| 변수                 | 설명                   | 필수 여부                                   |
| -------------------- | ---------------------- | ------------------------------------------- |
| `CLAWOPS_API_KEY`    | API 키 (`sk_...`)      | 예 (생성자에 전달하지 않은 경우)            |
| `CLAWOPS_ACCOUNT_ID` | 기본 계정 ID (`AC...`) | 예 (생성자에 전달하지 않은 경우)            |
| `CLAWOPS_BASE_URL`   | API 기본 URL           | 아니오 (기본값: `https://api.claw-ops.com`) |
| `OPENAI_API_KEY`     | OpenAI API 키          | OpenAI Realtime 사용 시                     |
| `GOOGLE_API_KEY`     | Google API 키          | Gemini Realtime 사용 시                     |

## 문서

- **[AI Agent 가이드](docs/agent/)** — 음성 에이전트 상세 사용법, 파이프라인 모드, 커스텀 제공자, MCP 연동
- **[트러블슈팅](docs/agent/troubleshooting.md)** — SSL 인증서 에러, 연결 실패 등 문제 해결

## 다른 언어

| 언어 | 패키지 | 저장소 |
|------|--------|--------|
| Node.js / TypeScript | [`@teamlearners/clawops`](https://www.npmjs.com/package/@teamlearners/clawops) | [sdk-node](https://github.com/koreaaitel/sdk-node) |

## 요구사항

- Python 3.9+
- `httpx` >= 0.23.0
- `pydantic` >= 2.0.0
- `aiohttp` >= 3.9.0 (Agent 사용 시)

## 라이선스

Apache-2.0
