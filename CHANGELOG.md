# Changelog

## 0.76.0 (2026-11-12)

### Changed
- **동작 변경: LiveKit 세션의 내장 도구 `hang_up` 이 작별 인사 재생을 마친 뒤 끊습니다.** 모델이 한 응답에서 작별 인사를 말하고 `hang_up` 을 부르면, 그 인사가 다 재생될 때까지 기다렸다가 끊습니다. 최대 15초 기다리고, 그 안에 끝나지 않으면 그대로 끊습니다. 종전에는 바로 끊어서, 인사가 합성 도중 잘려 발신자에게는 인사 없이 끊긴 것으로 들렸습니다.
  - 기다리는 동안 발신자 말로 인사가 끊기지 않습니다. `hang_up` 을 부르기 전에 이미 끼어들기가 일어났으면 끊지 않습니다.
  - 끊은 뒤에는 모델이 응답을 더 만들지 않습니다.
  - 자체 파이프라인·realtime 세션은 그대로입니다.

## 0.75.0 (2026-11-09)

### Added
- **`LiveKitSession.set_playout_gate(event)`.** 플랫폼이 보관 중인 오디오를 실제로 내보내기 시작하는 순간 `set` 되는 이벤트를 답니다. 이벤트가 `set` 되기 전에는 재생 완료(mark) 대기 시간을 세지 않습니다. 상대가 받기 전에 인사를 만들어 두는 발신에서, 벨이 길면 인사가 들리기도 전에 「다 말했다」로 처리되던 문제를 막습니다. 이벤트 전에는 들린 위치를 0 으로 보고, `set` 되는 순간부터 잽니다. `max_wait`(기본 900초) 안에 끝내 `set` 되지 않으면 들리지 않은 것(끊김)으로 봅니다. 달지 않으면 종전과 같습니다. mark 가 이벤트보다 먼저 오면 그대로 재생 완료로 봅니다.

## 0.74.0 (2026-11-09)

### Added
- **`IncomingCall.transfer(to, ...)` — `on_incoming` 에서 AI 없이 받아 바로 넘깁니다.** AI 세션을 열지 않으므로 첫 인사·모델 연결이 없고, 발신자는 연결음을 듣습니다. 통화가 연결되면 곧바로 전환합니다. 인자는 `call.transfer()` 와 같고 `mode="blind"`·`after_transfer="terminate"` 로 고정됩니다. 지정한 발신자만 AI 가 받고 나머지는 상담 번호로 보낼 때 씁니다. 이 통화에는 `call_start`/`call_end` 등 이벤트가 없습니다.

## 0.73.0 (2026-11-04)

### Changed
- **동작 변경: LiveKit 세션의 내장 도구 `transfer_call` 이 `after_transfer="return"` 이면 전환 결과를 돌려줍니다.** 전환이 끝날 때까지 기다렸다가 `{"status": ..., "message": ...}` 를 모델에 줍니다. 종전에는 `transfer_initiated` 를 바로 돌려줘, 대상이 받지 않아도 모델이 그 사실을 몰랐습니다. control 연결이 끊겨 결과를 알 수 없으면 `status` 가 `unknown` 입니다. `terminate` 와 자체 파이프라인·realtime 세션은 그대로입니다.

## 0.72.0 (2026-11-04)

### Added
- **`MediaWebSocket(on_event=...)`.** `start`·`media`·`dtmf`·`mark`·`stop` 이 아닌 메시지를 받는 콜백입니다. 지정하지 않으면 종전처럼 버립니다.

## 0.71.1 (2026-11-03)

### Fixed
- **control 연결이 다시 붙는 사이에 전환하면 `AttributeError` 가 나던 문제.** 연결이 끊겨 있으면 전환이 `AgentConnectionError`
  (`AgentError` 계열)로 바로 실패합니다. 메시지는 `transfer 불가` 로 시작합니다. 전송 중 끊긴 경우도 같은 오류입니다
  (종전에는 `aiohttp.ClientError`·`ConnectionResetError` 가 그대로 나갔습니다).
- **`AgentError`·`AgentConnectionError` 를 `clawops.agent` 에서 import 할 수 있습니다.** 종전에는 비공개 모듈 `clawops._exceptions` 에서만 가져올 수 있었습니다. 그 경로도 계속 됩니다.

## 0.71.0 (2026-10-28)

### Fixed
- **API 키가 폐기·만료됐거나 번호가 계정 것이 아니면 에이전트가 끝없이 재연결하던 문제.** 서버가 control 연결을
  거절하면(HTTP 400·401·403) 다시 시도하지 않고 `AgentAuthRejectedError` 로 멈춥니다. 다시 붙어도 결과가 같기 때문입니다.
  종전에는 1초부터 30초 간격으로 영원히 다시 시도해, 에이전트가 콜을 못 받는 상태가 로그 경고로만 남았습니다.
  - `agent.connect()`·`serve()` 가 곧바로 `AgentAuthRejectedError` 를 던집니다. 종전에는 10초 뒤 `TimeoutError` 가 났고
    연결 시도는 뒤에서 계속됐습니다.
  - 실행 중에 키가 폐기되거나 번호가 회수되면 `serve()` 가 진행 중 통화를 마친 뒤 이 오류로 끝납니다(정상 종료로 묻히지 않습니다).
  - `err.code` 로 사유를 구분합니다: `api_key_invalid`·`api_key_missing`·`number_not_owned`·`number_missing`.
  - 429·5xx·네트워크 오류는 종전처럼 다시 연결합니다.

## 0.70.0 (2026-10-23)

키패드(DTMF) 입력을 Twilio `<Gather>`·LiveKit `GetDtmfTask`·Pipecat `DTMFAggregator` 와 같은 방식으로 맞췄습니다.

### Changed
- **동작 변경: 키패드 자동 전달의 기본 대기가 0.5초에서 2초로 바뀝니다(`passive_dtmf_debounce_ms=2000`).**
  누른 키는 마지막 키 뒤 2초가 지나면 `[DTMF 입력: 534]` 로 모델에 전달됩니다. 0.5초는 사람이 누르는 간격보다 짧아
  `534` 가 `5`·`3`·`4` 로 나뉘어 들어갔습니다. 종전 값이 필요하면 `passive_dtmf_debounce_ms=500` 을 넣으세요.
- **동작 변경: `#` 을 누르면 그때까지 누른 키를 바로 전달합니다(`passive_dtmf_finish_key="#"`).** `#` 은 값에 들어가지 않습니다.
  아무 키 없이 `#` 만 누르면 `[DTMF 입력: #]` 를 전달합니다. 종전처럼 `#` 을 일반 키로 쓰려면 `passive_dtmf_finish_key=None` 을 넣으세요.

### Added
- **`ClawOpsAgent(passive_dtmf_max_digits=...)`** — 그 자리 수가 모이면 바로 전달합니다. 받을 자리 수가 항상 같을 때만 쓰세요.
  넘치게 누른 키는 다음 입력으로 넘어갑니다.

### Fixed
- **`collect_dtmf` 가 수집 전에 누른 키를 앞에 붙이던 문제.** 수집이 열린 뒤 누른 키만 받습니다. 그 전에 누른 키는 모델에만 전달됩니다.
- **`collect_dtmf` 대기가 안내 음성이 재생되는 동안 흐르던 문제.** `timeout`·`max_wait` 는 안내 음성 재생이 끝난 뒤부터 셉니다.
  재생 중에 키를 누르면 안내를 멈추고 그 키부터 받습니다.
- **`collect_dtmf` 가 실행되는 동안 대기음(`tool_config.hold_audio`)이 나가던 문제.** 키를 받는 동안에는 대기음을 틀지 않습니다.

## 0.69.0 (2026-10-21)

### Added
- **`transcript_delta` 이벤트 — 말하는 도중의 텍스트 (`LiveKitSession`).** `@agent.on("transcript_delta")` 로
  `(call, role, text)` 를 받습니다. `text` 는 그 발화의 **지금까지 누적 문장**이라 받을 때마다 통째로 바꿔 표시하면 됩니다.
  - `role="assistant"`: 오디오 재생 속도에 맞춘 에이전트 텍스트입니다. 종전에는 `TranscriptSynchronizer` 가 만들고 버리던 값입니다.
  - `role="user"`: 발신자 중간 전사(`user_input_transcribed`, `is_final=False`)입니다. 앞부분이 고쳐 쓰일 수 있습니다.
  - 발화가 끝나면 지금처럼 `transcript` 가 확정 텍스트를 보냅니다. 끊긴 발화는 확정이 마지막 `transcript_delta` 보다 짧을 수 있습니다.

## 0.67.0 (2026-10-20)

### Added
- **`sender_numbers` 리소스 — 문자 발신번호 조회·등록·삭제 (clawops#1489).** sync·async 모두, `accounts(id).sender_numbers` 도 같습니다.
  - `list()` — 등록된 발신번호(`sender_numbers`), 유예 기간인 번호(`grace`), 070번호·대표번호 회선 수 한도(`limit`)
  - `create(number=...)` — ClawOps에서 개통한 070번호·대표번호를 등록합니다. 즉시 승인됩니다.
  - `delete(sender_number_id)` — API 로는 ClawOps에서 개통한 번호만 삭제할 수 있습니다.
  - 발신번호 범위를 지정한 API 키는 조회만 됩니다.
- **`numbers.create()` 반환형 `ProvisionedPhoneNumber` 의 `sms_sender`.** 발급한 번호를 문자 발신번호로 자동 등록했는지와 못 했을 때의 `reason` 입니다.
  `registered` 가 `False` 여도 번호 발급은 성공입니다. `ProvisionedPhoneNumber` 는 `PhoneNumber` 의 하위 클래스라 기존 코드는 그대로 동작합니다.
- `ClawOpsErrorCode` 에 발신번호 오류 코드(`sender_number_*`, `api_key_sender_numbers_restricted`)를 더했습니다.

## 0.66.0 (2026-10-20)

### Added
- **`GeminiRealtime(input_audio_transcription=...)`** — Live 세션의 `input_audio_transcription` 에 그대로 실립니다.
  `language_codes`(BCP-47 언어 코드 목록)와 `custom_vocabulary`(우선 인식할 용어, 최대 1,000개)를 넣을 수 있습니다.
  지정하지 않으면 지금처럼 빈 설정(`{}`)을 보냅니다.

  ```python
  session = GeminiRealtime(
      model="gemini-3.8-live",
      input_audio_transcription={
          "language_codes": ["ko-KR"],
          "custom_vocabulary": ["클로옵스", "ClawOps"],
      },
  )
  ```

### Changed
- **`google-genai` 하한을 2.13.0 으로 올렸습니다**(`[gemini]` extra). `custom_vocabulary` 는 2.13.0 부터 받습니다.

## 0.65.0 (2026-10-17)

### Added
- **수신 통화의 착신전환 원번호 `forwarded_from` (clawopshq/clawops#1423).** 착신전환으로 들어온 통화에서 발신자가
  원래 건 번호입니다. `IncomingCall`(`@agent.on_incoming`, 받기 전)과 `CallSession`(받은 뒤) 양쪽에서 읽을 수 있습니다.
  - 착신전환을 실행한 통신사가 전환 정보를 함께 보내온 경우에만 값이 있고, 직접 걸려온 통화와 발신 통화는 `None` 입니다.
  - 서버가 이 값을 보내기 전 버전에서도 `None` 이라, 기존 코드는 영향을 받지 않습니다.

  ```python
  @agent.on("call_start")
  async def on_start(call):
      if call.forwarded_from:
          print(f"착신전환 통화 — 원래 건 번호 {call.forwarded_from}")
  ```

## 0.64.0 (2026-10-13)

### Added
- **`Call` 에 전환(담당자 연결) 필드 타입(clawopshq/clawops#1215).** 서버는 이미 내려 주고 있었지만 모델에 필드가
  없어 `extra="allow"` 로만 받던 값입니다.
  - 요약(대표 시도 하나): `transfer_to` · `transfer_status` · `transfer_duration` · `transfer_hangup_cause_q850` ·
    `transfer_sip_response_code` · `transfer_reason_text`. 목록·단건 조회 모두에 있습니다.
  - 전체 시도: `transfers: list[TransferLeg]` — **`calls.get()` 단건 조회에만** 있습니다(목록에서는 `None`).
  - `TransferLeg.answered_at`(대상이 받은 시각)과 `TransferLeg.timeout`(적용한 대기 한도, 초)이 새로 생겼습니다.
    담당자가 받기까지 걸린 시간은 `answered_at - started_at` 입니다. `connected_at` 은 warm 전환에서 연결 전
    안내가 끝난 뒤 시각이라 그만큼 늦습니다.

  ```python
  call = client.calls.get("CA...")
  for leg in call.transfers or []:
      if leg.answered_at and leg.started_at:
          secs = (leg.answered_at - leg.started_at).total_seconds()
          limit = f" (한도 {leg.timeout}초)" if leg.timeout is not None else ""
          print(f"#{leg.sequence} {leg.status} — {secs:.1f}초 만에 받음{limit}")
  ```

  - **받았는지 판정은 `answered_at` 으로 합니다.** 값이 있으면 받은 것입니다 — 연결 전 안내 도중 끝난 warm
    전환은 `status` 가 `failed` 지만 받은 전환입니다. `answered_at` · `timeout` 은 2026-10-13 부터 기록돼,
    그 이전 전환(값이 `None`)만 `status` 로 판정하세요.
  - `refer-attended` 는 기다리는 단계가 없어 `timeout` 이 항상 `None` 입니다.
  - 요약 필드가 `None` 이어도 전환이 없었다는 뜻은 아닙니다. 대표 시도는 끝난 시도 중에서만 고르므로, 시도가
    진행 중이거나 `interrupted` 로만 끝났으면 `None` 입니다. 전환 여부는 `transfers` 로 판정하세요.
  - `transfer_reason_text` 는 대표 시도가 아니라 마지막으로 기록된 통신망 사유입니다. 시도별 사유는
    `transfers[].reason_text` 를 쓰세요.
  - `status` · `mode` 는 문자열입니다(`Literal` 이 아님). `TransferLeg` 의 필드는 전부 `Optional` 이라, 시도 하나의
    모양이 어긋나도 통화 조회 전체가 실패하지 않습니다.
  - 시도 하나의 값 타입이 예상과 달라도(소수 `duration` 등) 통화 조회 전체가 실패하지 않습니다. 그 시도는 검증
    없이 받은 값 그대로 담깁니다.
  - Node SDK 0.52.0 과 같은 필드입니다.

### Changed
- ⚠️ **이 필드들을 extra 로 읽던 코드는 고쳐야 합니다.** 0.63.0 까지는 모델에 없는 값이라 camelCase 이름과
  dict 로 읽혔습니다. 이제는 snake_case 필드와 `TransferLeg` 객체입니다.

  | 0.63.0 까지 | 0.64.0 부터 |
  |---|---|
  | `call.transferStatus` | `call.transfer_status` |
  | `call.transfers[0]["answeredAt"]` | `call.transfers[0].answered_at` (`datetime`) |
  | `call.model_dump()` 의 `transferStatus` 키 | `transfer_status` 키 (camelCase 는 `model_dump(by_alias=True)`) |

## 0.63.0 (2026-10-11)

### Added
- **`@agent.on_incoming` — 수신 통화를 받기 전에 거절합니다(clawopshq/clawops#1251).** 훅이 `IncomingCall` 을
  받아 `await call.reject()` 를 부르면 SDK 가 `call.accept` 대신 `call.reject` 로 답하고, 서버는 응답하기
  전에 통화를 끝냅니다(요금 없음). 세션·미디어·`session_setup` 은 열리지 않습니다.

  ```python
  from clawops.agent import IncomingCall

  @agent.on_incoming
  async def screen(call: IncomingCall):
      if call.caller_id_restricted:   # 번호 표시제한 통화
          await call.reject()         # SIP 403. reason="busy" 면 SIP 486
  ```

  - `IncomingCall`: `call_id`·`from_number`·`to_number`·`caller_id_restricted`·`reject(reason="rejected")`.
    `reason` 은 `"rejected"`·`"busy"` 만 받습니다(그 밖은 `ValueError`). `CallSession` 이 아닙니다.
  - `reject()` 없이 끝나면 받습니다. 예외를 던지거나 5초 안에 결정하지 못하면 경고를 남기고 **받습니다**.
    시한 뒤의 `reject()` 와 두 번째 `reject()` 는 무시됩니다.
  - 훅을 등록하지 않으면 지금과 똑같이 즉시 받습니다. `agent.on_incoming(fn)` 직접 등록과 동기 함수도 됩니다.
  - 훅은 control 연결의 수신 루프와 따로 돌아, 선별하는 동안 다른 통화의 이벤트를 막지 않습니다.

> ⚠️ 거절은 서버가 `call.reject` 를 지원해야 동작합니다. 서버 배포 전에는 거절이 다른 경로로 넘어갈 수 있습니다.

## 0.62.0 (2026-10-10)

### Fixed
- **긴 통화에서 Gemini 연결이 끊기면 통화가 무음으로 남던 문제.** Gemini Live 는 WebSocket 연결이 약 10분에서
  끝나고(끝나기 전 `go_away`), 압축 없는 오디오 세션은 15분에서 끝납니다(공식 문서). 지금까지는 그 순간
  세션만 정리하고 통화는 그대로 두어, 고객이 무음을 듣다 끊었습니다. 이제 LiveKit `livekit-plugins-google` 과
  같은 방식으로 이어 갑니다.
  - 접속 때마다 `session_resumption` 핸들을 보내고 서버가 주는 새 핸들을 보관합니다.
  - `go_away` 를 받으면 새 연결을 열어 대화를 이어 갑니다. 예기치 않게 끊겨도 같은 경로로 재접속합니다(기본 3회,
    백오프 1·2·4초). 옛 연결과 실패한 시도의 연결은 반드시 닫습니다 — 안 닫으면 서버가 세션을 붙잡아 다음 시도가
    1008 로 거절됩니다(실측).
  - 핸들 재접속이 끝내 거절되면 핸들 없이 새 세션으로 통화를 이어 갑니다. 대화 기억은 잃지만 system_prompt 와
    도구는 그대로이고, 끊기는 것보다 낫습니다.
  - 재접속이 끝내 실패하면 통화에 `session_lost` 이벤트(`call.on("session_lost", handler(call, reason))`)를
    낸 뒤 통화를 끊습니다. 모델만 사라진 채 통화가 이어지는 일은 없습니다.
  - 재접속 구간에는 몇 초의 공백이 생길 수 있고, 그 사이 들어온 음성은 모델에 전달되지 않습니다.

### Added
- **`GeminiRealtime(context_window_compression=...)`** — Gemini `ContextWindowCompressionConfig` 그대로 전달.
  15분 넘는 통화에는 `{"sliding_window": {}}` 를 권장합니다. 지정하지 않으면 보내지 않습니다.
- **`GeminiRealtime(session_resumption=True, reconnect_max_attempts=3)`** — 재접속을 끄거나 시도 상한을 바꿉니다.
- `[gemini-trace]` 에 `session_resumption_update`·`reconnect`·`reconnected`·`session_lost` 가 찍힙니다.

## 0.61.0 (2026-10-10)

### Added
- **도구별 `behavior`·`scheduling` — `@agent.tool(behavior="NON_BLOCKING", scheduling="SILENT")`.**
  Gemini Live 의 비동기 함수 호출을 도구 단위로 켭니다. 생략하면 세션 기본값(모델별, 0.60.0)을 따릅니다.
  `@agent.tool` 과 `@agent.tool(...)` 둘 다 됩니다. 다른 세션(OpenAI 등)은 두 값을 무시합니다.

  ```python
  @agent.tool(behavior="NON_BLOCKING", scheduling="SILENT")
  async def save_note(text: str):
      """통화 중 메모를 저장합니다."""
      await crm.save(text)   # 모델은 기다리지 않고 대화를 이어 간다. 결과는 말하지 않고 기억만.

  @agent.tool                 # 생략 = BLOCKING(3.8-live 기본). 결과가 다음 문장이므로 모델이 기다린다.
  async def lookup_order(order_id: str) -> str:
      ...
  ```

  - `behavior`: `"BLOCKING"`(결과를 받을 때까지 모델이 기다림) · `"NON_BLOCKING"`(도구가 도는 동안 모델이 계속 대화).
  - `scheduling`(NON_BLOCKING 에만): `"SILENT"`(말하지 않고 기억) · `"WHEN_IDLE"`(지금 말을 마친 뒤 전달, 서버 기본값) · `"INTERRUPT"`(즉시 끼어들어 전달).
  - 핸들러가 `None` 을 돌려주면 「전할 말이 없다」로 보고 NON_BLOCKING 결과를 `SILENT` 로 보냅니다(따로 정하지 않았을 때).
- **`GeminiRealtime(tool_response_scheduling=...)`** — NON_BLOCKING 결과의 세션 기본 `scheduling`. 도구별 값이 우선합니다.
- **NON_BLOCKING 도구 실행 런타임.** 비동기 도구는 수신 루프를 막지 않고 바로 태스크로 돌리며, 끝나는 즉시
  응답을 보냅니다(300ms drain 대기·hold audio 없음 — 모델 음성과 겹치지 않게). 서버가 `toolCallCancellation`
  (끼어들기 등)을 보내면 진행 중인 태스크를 취소하고 응답을 보내지 않습니다. BLOCKING 도구는 지금처럼
  drain 뒤 실행하고 hold audio 를 틉니다. 한 호출 묶음에 둘이 섞이면 갈라서 처리합니다.

### Changed
- 내장 도구(`hang_up`·`collect_dtmf`·`send_dtmf`·`transfer_call`)는 세션 `tool_behavior` 가 NON_BLOCKING 이어도
  **BLOCKING** 으로 선언합니다 — 끊기·전환 중에 모델이 말을 이어 가면 음성이 잘립니다. 예외는
  `gemini-3.8-live-extended-thinking`(NON_BLOCKING 만 받음) — 이 모델에서는 모든 도구가 NON_BLOCKING 이고
  도구별 `BLOCKING` 요청은 경고 뒤 NON_BLOCKING 으로 바뀝니다.
- `gemini-3.8-live-extended-thinking` 에는 `scheduling` 을 절대 보내지 않습니다(서버가 1007 로 세션을 끊습니다).
  지정하면 경고만 남기고 뺍니다.
- 서버가 `id` 없이 보낸 함수 호출에는 응답에도 `id` 를 넣지 않습니다.
- 출력 오디오 잔여 프레임을 `turn_complete` 뿐 아니라 `generation_complete` 에서도 flush 합니다.
  `turn_complete` 는 모델이 「재생이 끝났다」고 믿는 시점까지 미뤄지고 3.8 에서는 오지 않는 경우가 보고됐습니다.
- 핸들러가 `None` 을 돌려주면 `"None"` 문자열 대신 `{"result": "ok"}` 를 보냅니다.

## 0.60.0 (2026-10-10)

### Fixed
- **`gemini-3.8-live` 에서 도구 실행 중 모델이 계속 말하던 문제 — 함수 선언에 `behavior` 를 명시합니다.**
  Gemini 3.8 Live 는 함수 호출 기본값이 비동기(`NON_BLOCKING`)로 바뀌었습니다(모델 페이지
  「Migrating from Gemini 3.1 Flash Live」). SDK 가 `behavior` 를 보내지 않아 서버는 도구를 비동기로
  돌리고, 도구 결과를 기다리는 사이 모델이 한 말이 결과 뒤에 몰려 나와 발화가 겹친 것처럼 들렸습니다.
  이제 모델별 기본값을 함수 선언(커스텀·MCP·내장 도구 전부)에 싣습니다.

  | 모델 | 기본 `behavior` |
  | --- | --- |
  | `gemini-3.8-live` | `BLOCKING` — 3.1 과 같은 동기 동작 복원 |
  | `gemini-3.8-live-extended-thinking` | `NON_BLOCKING` — 이 모델은 `BLOCKING` 을 거절합니다 |
  | 그 외(`gemini-3.1-flash-live-preview` 등) | 보내지 않음(서버 기본값 = 동기) |

### Added
- **`GeminiRealtime(tool_behavior=...)`** — `"BLOCKING"` 또는 `"NON_BLOCKING"` 으로 모델별 기본값을
  덮어씁니다. 모든 도구에 같은 값이 실립니다(도구별 지정과 비동기 도구 실행 런타임은 다음 버전).
- **`GeminiRealtime(proactive_audio=...)`** — `proactivity.proactive_audio` 에 그대로 실립니다.
  3.8 두 모델은 영구 ON 이라 `False` 를 보내면 거절되고, 3.1 은 미지원입니다. 지정하지 않으면 보내지 않습니다.
- **`GeminiRealtime(api_version=...)`** — google-genai 클라이언트의 `http_options.api_version`
  (기본 `v1beta`)을 바꿉니다.
- **`CLAWOPS_GEMINI_DEBUG=1`** — Live 서버 메시지(오디오 바이트 수·턴 완료·생성 완료·끼어들기·전사·
  도구 호출/취소)와 도구 응답 전송을 `[gemini-trace] +NNNms ...` 한 줄씩 INFO 로 남깁니다.
  발화 겹침 같은 타이밍 문제를 보고할 때 이 로그를 함께 보내 주세요.

## 0.59.0 (2026-09-29)

### Added
- **수신 통화의 발신번호 표시제한 여부 — `call.caller_id_restricted`.** 발신자가 번호
  표시제한(`*23#` 등)을 걸고 건 수신 통화면 `True` 입니다. 번호는 지금처럼 `call.from_number`
  에 그대로 전달되고, 표시제한 여부만 이 값으로 알 수 있습니다.

  ```python
  @agent.on("call_start")
  async def on_start(call):
      if call.caller_id_restricted:
          ...  # 발신자가 번호를 숨기려 한 통화 — 되걸기·번호 저장 전에 확인
  ```

  - 통신사가 표시제한 여부를 보내지 않은 통화와 발신 통화는 `False` 입니다.
  - 이 값을 보내기 전의 서버에 연결하면 항상 `False` 입니다.
- 통화 조회(`calls.get`·`calls.list`) 모델 `Call` 에 `caller_id_restricted`(`bool | None`) 를
  추가했습니다. 발신 통화와 2026-09-29 이전의 수신 통화는 `None` 입니다.

## 0.58.0 (2026-09-29)

### Added
- **Gemini thinking 설정 — `GeminiRealtime(thinking_config=...)`.**
  `gemini-3.8-live-extended-thinking` 은 생각 수준(`thinking_level`)이 없으면 세션을 열 때
  `Thinking level must be specified for this model.` 로 거절됩니다. 지금까지는 이 값을 넘길 방법이
  없어 이 모델을 쓸 수 없었습니다. 값은 해석하지 않고 Gemini 에 그대로 넘기며, 지정하지 않으면
  보내지 않습니다(기존 동작 그대로).

  ```python
  GeminiRealtime(
      model="gemini-3.8-live-extended-thinking",
      thinking_config={"thinking_level": "LOW"},
  )
  ```

  받는 값은 모델마다 다릅니다 — `gemini-3.8-live` 는 `thinking_level` 을 거절합니다.
  모델별 표는 `docs/agent/quickstart.md` 에 있습니다.

## 0.57.2 (2026-09-28)

### Security
- **미디어 WebSocket 이 계정 API 키를 싣지 않습니다.** `MediaWebSocket` 이 연결할 때
  `Authorization: Bearer <API 키>` 를 붙이고 있었는데, 미디어 스트림 인증은 서버가 주는 URL 의
  1회용 `token` 하나이고 서버는 이 헤더를 읽지 않았습니다. 한편 서버가 2026-09-27 까지
  `ws://` URL 을 내려주고 있어, 첫 핸드셰이크(301 리다이렉트 전)가 평문이었고 그 요청에
  API 키가 실려 나갔습니다. 서버는 2026-09-27 부터 `wss://` 를 내려주도록 고쳤습니다
  (clawops#1250).

  이제 헤더를 보내지 않습니다. `MediaWebSocket(api_key=...)` 는 호환을 위해 받기만 하고
  쓰지 않으며 선택 인자가 되었습니다. 공개 호스트로 가는 `ws://` URL 을 받으면 경고 로그를
  남깁니다(연결은 그대로 합니다).
- **미디어 WebSocket 연결 로그에 1회용 `token` 을 남기지 않습니다.** `Media WS connected:`
  로그가 `?token=...` 이 붙은 URL 전체를 info 로 찍고 있었습니다. 이제 query·fragment 를
  떼고 찍습니다.

## 0.57.1 (2026-09-25)

### Fixed
- ⛔ **Python 3.9·3.10 에서 `clawops.agent` 를 import 하는 순간 실패하던 것.**
  패키지는 `Requires-Python: >=3.9` 로 배포되는데, 에이전트 모듈이 3.11 부터 있는
  `enum.StrEnum` 과 3.10 부터 있는 `dataclass(slots=True)` 를 쓰고 있었습니다.
  0.14.4 부터 0.57.0 까지 해당합니다. `import clawops` 와 REST 클라이언트는 영향이
  없었습니다.

  이제 3.9 부터 선언대로 동작합니다. `BuiltinTool` 은 `str` 을 상속한 `Enum` 이 되었고
  `str(BuiltinTool.HANG_UP) == "hang_up"` 등 동작은 그대로입니다. CI 가 3.9 에서
  `clawops.agent` 하위 모듈을 전부 import 해 보도록 넓혀 같은 일이 되풀이되지 않게 했습니다.
- ⛔ **Python 3.9 에서 `@agent.tool` 을 붙이는 순간 `AttributeError` 로 실패하던 것.**
  도구 등록·호출이 3.10 부터 있는 `inspect.get_annotations` 를 쓰고 있었습니다(import 는
  통과해 위 항목을 고쳐도 남아 있었습니다). CI 가 3.9 에서 도구 등록·호출 테스트를 실제로
  돌립니다.

## 0.57.0 (2026-09-18)

### Added
- **전환이 연결되지 않으면 발신자에게 한마디 — `call.transfer(to, failure_message=...)`.**
  전환이 무응답·통화 중·대상 실패로 끝날 때, 끊기 전에 그 문장을 **발신자에게** 들려준 뒤
  통화를 종료합니다. 지금까지는 아무 말 없이 끊겼습니다.

  ```python
  await call.transfer(
      "01012345678",
      failure_message="죄송합니다. 담당자와 연결되지 않았습니다. 잠시 후 다시 걸어 주세요.",
  )
  ```

  - `whisper` 와 **듣는 사람이 반대**입니다. `whisper` 는 전화를 **받은** 대상에게만 들리므로
    아무도 받지 않은 경우를 덮지 못합니다. 둘은 함께 쓸 수 있습니다.
  - `failure_voice` 로 읽을 음성을 고를 수 있습니다(`<Say>` 의 `voice` 와 같은 형식).
    생략하면 기본 음성입니다.
  - `after_transfer="return"` 에서는 재생되지 않습니다 — AI 가 통화를 이어받으므로 무엇을
    말할지는 여러분 코드가 정합니다. 다만 이어받을 채널이 없어 서버가 `terminate` 로
    내려앉힌 통화에서는 재생됩니다(그게 발신자가 무음에 남는 경우입니다).
  - 주지 않으면 payload 에 키를 붙이지 않습니다 — 기존 동작 그대로입니다.

  Node SDK 0.45.0 `failureMessage` 와 같습니다. 서버 구현은 `clawopshq/clawops#1219`.

## 0.56.0 (2026-09-13)

### Added
- **전사 어휘 힌트 — `OpenAIRealtime(transcription_prompt=...)`.** 발신자 음성 전사에
  어휘 힌트(자유 문장)를 넘깁니다. 병원 진료 용어·업무 용어처럼 일반 전사가 자주 틀리는
  단어를 적어 두면 `transcript` 이벤트의 전사 텍스트가 그 어휘 쪽으로 맞춰집니다.

  ```python
  OpenAIRealtime(
      system_prompt="...",
      transcription_prompt="재진, 초진, 예약 변경, 직원 연결",
  )
  ```

  - **전사 텍스트에만 영향을 줍니다.** 모델이 오디오를 알아듣는 방식은 바뀌지 않습니다 —
    모델이 알아야 할 어휘는 `system_prompt` 에 함께 적어 주세요.
  - 기본은 `None`(보내지 않음)이라 기존 동작은 그대로입니다. 빈 문자열도 보내지 않습니다.

### Changed
- **LiveKit Agents 실행(`clawops.agent.livekit.LiveKitSession`)이 정식 지원입니다.**
  문서의 「실험적」 표기를 뗐습니다.

## 0.55.0 (2026-09-10)

**배포에서 전화가 죽던 구간과, 배포가 통화를 끊던 구간이 둘 다 사라집니다.**
앞의 것은 후임이 붙을 때까지 자리를 지켜서, 뒤의 것은 마감을 없애서입니다.
그리고 배포 사고 중 지표에 안 잡히던 둘을 시끄럽게 만듭니다.

### Changed
- ⭐ **`serve()` 가 종료 시그널을 받아도 자리를 바로 놓지 않습니다.** 후임이 붙을 때까지
  최대 20초(`handover_wait`) 자리를 들고 **계속 전화를 받습니다.** 후임이 붙으면 서버가
  인계를 통지하고, 그때 드레이닝으로 넘어갑니다.

  예전에는 SIGTERM 을 받는 즉시 자리를 놓았습니다. 그때 후임이 아직 안 떠 있으면 그 사이
  오는 전화가 전부 죽었습니다 — readinessProbe 는 그 순간이 오지 않게 오케스트레이터를
  붙잡아 두는 우회였습니다.

  실측(겹치는 배포 · 프로브 없음 · 앱 기동 12초): **실패 6통·불통 8초 → 0통·불통 없음.**
  앱 기동이 `handover_wait` 보다 길면 그 차이만큼은 남습니다(기동 35초 → 불통 11초) —
  그래서 readinessProbe 는 여전히 권장합니다.

- ⭐ **인계를 받은 뒤에는 연결을 유지한 채 드레이닝합니다.** 자리는 이미 서버가 가져갔으므로
  새 전화는 후임에게 가고, 진행 중이던 통화의 종료 이벤트는 이 연결로 돌아옵니다.
  그래서 **드레이닝 중에 끝난 통화도 `ended_duration` 이 채워집니다** — 예전에는 항상
  `None` 이었습니다.

- ⭐ **마감이 사라졌습니다 — 진행 중이던 통화를 우리가 끊지 않습니다.**
  `drain_timeout`(120초)과 `shutdown_deadline`(110초)이 기본값에서 없어집니다.

  그 값들은 **ECS `stopTimeout` 상한(120초)에서 나온 숫자**인데 k8s 에도 그대로 쓰였습니다.
  k8s 의 `terminationGracePeriodSeconds` 에는 상한이 없습니다. 실측하면 진행 중이던 통화의
  **29.8%(464/1,559)가 110초를 넘습니다** — 기다렸으면 끝났을 통화까지 SDK 가 자르고
  있었습니다.

  이제 끊는 주체는 **플랫폼 SIGKILL 하나**입니다. 유예를 통화 길이에 맞게 잡아 주세요.
  실측(130초 통화 · 진짜 SIGTERM): SIGTERM 후 **131초를 버티고 잘린 통화 0**.

  마감을 주면 규약은 그대로입니다 — **두 값을 더하지 않습니다.** SIGTERM 부터
  `shutdown_deadline` 하나를 세고, 인계 대기가 쓴 만큼 드레이닝의 몫이 줄어듭니다.
  유예가 짧은 환경(ECS 는 `stopTimeout` 이 120초 상한)에서 유예 안에 스스로 정리하려면
  그 유예보다 조금 짧게 주세요.

  > ⚠️ 마감이 없어도 **빠져나올 길은 있습니다.** 종료 시그널 2차 = 인계 중단,
  > 3차 = 진행 중 통화 절단. SIGINT 는 1차가 곧 2차 몫이라 Ctrl-C 두 번이면 끊깁니다.

- **물러나기로 한 뒤에는 어떤 이유로 끊겨도 재연결하지 않습니다.** 게이트웨이 롤링(1001)이나
  네트워크 절단에 재연결하면 그 사이 자리를 받은 후임을 밀어냅니다 — 후임은 종료 시그널도
  안 받았는데 물러나게 됩니다. 재연결하는 경로가 남아 있을 때를 대비해 연결 URL 에
  `role=retiring` 을 실어 서버가 자리를 주지 않게 합니다.

- 시그널 단계가 명확해졌습니다. **SIGTERM** 1차 = 인계 대기, 2차 = 인계를 더 기다리지 않고
  드레이닝, 3차 = 진행 중 통화까지 끊기. **SIGINT** 는 예전 그대로 곧바로 드레이닝합니다 —
  로컬에서 Ctrl-C 를 눌렀는데 20초를 기다리면 안 됩니다.

> ⚠️ **후임을 띄우지 않고 내리기만 하는 배포**(스케일인·단순 정지)에서는 인계 대기가 손해입니다.
> 그 대기 동안 받은 전화가 유예 만료에 끊길 수 있습니다. 그런 배포라면 `handover_wait=0`
> 으로 끄면 예전 동작 그대로입니다.

### Added
- ⭐ **준비 표시를 SDK 가 직접 씁니다.** `connect()` 가 반환하는 시점이 곧 "콜을 받을 수 있게
  된 시점" 이고 **그걸 아는 것은 SDK 뿐**입니다. 그래서 애플리케이션 코드에서 이 줄이
  사라집니다:

  ```python
  await agent.connect()
  Path("/tmp/clawops-ready").touch()   # ← 더 이상 필요 없습니다
  ```

  자리를 넘기거나(인계) 드레이닝에 들어가면 **지웁니다** — 그게 프로브를 떨어뜨려
  오케스트레이터가 이 인스턴스를 뒤로 뺍니다. 경로는 `CLAWOPS_READY_FILE`(기본
  `/tmp/clawops-ready`), 빈 값이면 끕니다. 못 쓰면 경고만 남기고 기동은 계속합니다.

- **`serve(health_port=8080)` 으로 `/healthz` 를 엽니다.** 준비면 200, 아니면 503.
  distroless 처럼 `cat` 도 `sh` 도 없는 이미지에서는 exec 프로브가 성립하지 않아
  HTTP 프로브가 유일한 길입니다. `connect()` **전에** 열리므로 기동이 느려도 프로브는
  붙고 503 을 받습니다.

- 배포 사고 중 **지표에 안 잡히는 둘**을 시끄럽게 만듭니다. 동작은 바뀌지 않습니다.

### Added
- **종료 시그널이 프로세스에 닿는지 확인하고, 안 닿으면 경고합니다.** `CMD python app.py`
  처럼 셸 형태로 쓰면 PID 1 이 `/bin/sh` 가 되는데, 셸은 SIGTERM 을 자식에게 전달하지
  않습니다. 그러면 배포로 컨테이너를 내릴 때 SDK 는 종료를 듣지 못하고, 유예가 끝날
  때까지 새 콜을 계속 받다가 SIGKILL 로 **전부 끊깁니다.**

  이게 위험한 이유는 **불통 지표에 안 잡히기 때문**입니다. 저희 실측에서 45초 통화 기준
  26건이 통째로 잘렸는데 불통은 0 이었습니다 — 배달만 보면 아무 문제가 없어 보입니다.

  검사는 부모 프로세스를 따라 올라가며 봅니다(PID 1 만 보면 `shareProcessNamespace` 에서
  놓칩니다). `tini`·`dumb-init` 이 있으면 정상으로 보고 넘어가고, **컨테이너 안이 아니면
  아예 검사하지 않습니다** — 로컬 터미널은 부모가 늘 셸이라 매번 틀린 경고가 됩니다.

- **낡은 준비 표시 파일을 기동 시 지웁니다.** readinessProbe 로 준비 표시 파일을 쓰는
  배포에서, 컨테이너가 SIGKILL 로 죽으면 그 파일이 남습니다. `/tmp` 가 emptyDir 이면
  pod 수명 동안 남으므로 **새 프로세스가 연결되기도 전에 Ready 로 판정되고**, 그동안
  오는 콜이 죽습니다. `connect()` 가 시작될 때(=애플리케이션이 표시를 만들기 전) 한 번
  지웁니다. 경로는 `CLAWOPS_READY_FILE`(기본 `/tmp/clawops-ready`), 빈 값이면 끕니다.

### Changed
- 드레이닝 로그가 사후 판별에 쓸 수 있게 바뀌었습니다 — 시그널 수신 한 줄, 드레이닝
  시작·완료에 경과 시간, 상한 초과 시 플랫폼 유예를 늘리라는 안내.

## 0.54.0 (2026-09-09)

무중단 배포. 지금까지는 에이전트 서버를 재배포할 때마다 그 번호로 오는 콜이 **끊겼습니다.**
새 인스턴스를 먼저 띄워 겹치게 배포해도 나아지지 않았는데(오히려 나빠졌는데), 그 이유와
해결이 이번 버전입니다.

### Fixed
- ⛔ **번호를 넘겨받은 뒤에도 재연결해, 방금 인계받은 프로세스를 도로 밀어내던 것.**
  control 연결은 번호당 하나뿐이라, 새 인스턴스가 붙으면 서버가 옛 연결을 닫아 자리를
  넘깁니다. 그런데 SDK 가 close code 를 보지 않아 그것을 평범한 종료로 읽고 1초 뒤
  재연결했습니다 — 그러면 새 인스턴스가 밀려나고, 그쪽이 또 재연결합니다. 겹치는 창
  내내 자리를 주고받다가 서버의 재연결 제한에 걸려 **번호가 분 단위로 격리**됐습니다.

  이제 인계(`4409`)와 소유권 상실(`4403`)에는 재연결하지 않습니다. 그 밖의 종료
  (게이트웨이 교체 `1001`, 네트워크 절단 등)는 전과 같이 재연결합니다.

- ⛔ **종료 시그널에 진행 중인 통화를 끊던 것.** `serve()` 는 SIGTERM 을 받으면 통화
  도중이든 아니든 즉시 연결을 끊었습니다. 이제 `drain()` 을 거칩니다.

### Added
- **`await agent.drain(timeout=...)`** — 새 콜 수신을 멈추고, 이미 진행 중인 통화가 끝나기를
  기다린 뒤 종료합니다. 반환값은 `(스스로 끝난 수, 상한에 걸려 끊은 수)`.

  control 과 미디어가 별개 연결이라 가능합니다. control 을 닫는 것은 그 번호의 **배달
  자리**를 내놓는 것뿐이고, 이미 붙은 통화는 통화별 미디어 연결로 계속 흐릅니다.

  드레이닝 중에 끝나는 통화는 `call_end` 의 `ended_duration` 이 `None` 입니다 — 그 값은
  방금 닫은 control 연결을 타고 오기 때문입니다.

- **`on_taken_over`** 콜백과 **`agent.taken_over`** — 다른 프로세스가 이 번호를 넘겨받았을 때.
  `connect()` 를 직접 모는 경우에만 필요합니다(`serve()` 는 알아서 처리합니다).

### Changed
- **`serve()` 가 인계받았을 때도 반환합니다.** 인계 통지를 받으면 드레이닝을 거쳐 스스로
  종료합니다. 다만 **통지가 항상 오지는 않으므로**(접속하는 서버에 따라 갈립니다) 롤링
  배포에서 옛 인스턴스를 내리는 단계는 그대로 필요합니다 — 통지를 못 받은 인스턴스는 새
  콜을 받지 않는 상태로 남아 있다가 종료 시그널에서 드레이닝을 거쳐 끝납니다.
  `serve(drain_timeout=...)` 로 상한을 조절합니다(기본 120초). 두 번째 종료 시그널은
  기다리지 않고 즉시 끊습니다.

### 무중단으로 배포하려면

1. 이 버전 이상으로 올립니다.
2. **새 인스턴스를 먼저 띄우고 구 인스턴스를 내리도록** 바꿉니다.
   ECS: `minimumHealthyPercent: 100`, `maximumPercent: 200` · k8s: `maxUnavailable: 0`.
   내렸다가 올리면 그 사이는 여전히 콜을 받을 곳이 없습니다.
3. **종료 유예를 드레이닝 상한보다 길게** 둡니다.
   ECS `stopTimeout` · k8s `terminationGracePeriodSeconds` — 짧으면 드레이닝 도중
   SIGKILL 이 와서 통화가 끊깁니다.

## 0.53.0 (2026-09-07)

### Fixed
- ⛔ **이메일 수신거부 항목이 섞이면 목록 조회가 통째로 실패하던 것.** `channel` 을
  `Literal["call", "message"]` 로 굳혀 둔 탓에, 서버가 `email` 채널을 더한 뒤로는 고객이
  콘솔에서 이메일 주소를 하나만 등록해도 `blocked_recipients.list()` 가 **파싱 단계에서**
  깨졌습니다. 이 버전 이전을 쓰신다면 이메일 수신거부를 등록하기 전에 올려 주세요.

  응답의 `channel` 은 이제 `str` 입니다. **응답 타입을 좁히면 서버가 값을 늘리는 날 옛 SDK 가
  전부 깨진다**는 것이 이번 교훈이라, 같은 실수를 반복하지 않도록 회귀 테스트로 못을
  박았습니다. `BlockedChannel` 은 요청을 쓸 때의 도움말로 남습니다.
- `source` 에 `'ars'`(ARS 수신거부 9번)·`'sms'`(문자 회신) 처럼 **내부 접수 경로로 들어온
  값**이 오는 경우도 같은 부류였습니다(응답은 원래부터 `str` 이라 깨지진 않았지만,
  `BlockedRecipientSource` 목록에 `'agent'` 가 빠져 있었습니다).

### Changed
- ⚠️ **`number` → `recipient` (breaking).** 요청·응답·필터 모두 바뀌었습니다.

  ```python
  # before
  client.blocked_recipients.create(number="010-1234-5678", channel="call")
  # after
  client.blocked_recipients.create(recipient="010-1234-5678", channel="call")
  ```

  `number` 라는 이름에 이메일 주소를 담으면 **필드명이 거짓말을 합니다.** 그 값을 전화번호로
  가정한 코드가 조용히 깨집니다. 서버가 별칭을 남기지 않았으므로(외부 API 사용 이력 0건을
  확인하고 내린 결정) 이 SDK 도 남기지 않습니다.

### Added
- **`channel="email"`** — 이메일 수신거부. `recipient` 에 이메일 주소를 넣습니다(소문자로
  정규화되어 저장). 같은 상대라도 채널마다 별개 항목이라, 전화와 이메일을 모두 막으려면
  두 번 등록합니다.

  ⚠️ **막는 방식이 전화·문자와 다릅니다.** 전화·문자는 대상이 명단에 있으면 그 발신 요청
  전체가 `422 recipient_blocked` 로 거절되지만, 이메일은 **차단된 수신자만 빼고 나머지에게
  보냅니다.** 응답의 `suppressed` 에 빠진 주소가 담기므로 `2xx` 를 받아도 그 칸을 봐야
  누가 안 갔는지 알 수 있습니다. 수신자가 전원 걸렸을 때만 `422` 입니다.

## 0.52.0 (2026-09-06)

### Added
- **LiveKit 오디오 출력 일시정지(pause/resume).** `LiveKitSession(create, audio_pause=True)` 로
  켜면 `ClawOpsAudioOutput` 이 LiveKit 에 `can_pause` 를 알립니다. 그러면 LiveKit 의
  `resume_false_interruption`(기본 2초)이 실제로 동작합니다 — 회선 잡음·기침 한 번에 안내가
  끊겼다가, 정해진 시간 안에 실제 말이 없으면 **멈춘 자리부터 이어서** 재생됩니다. 지금까지는
  이 싱크가 일시정지를 못 내서 LiveKit 이 기능을 끄고 통화마다 경고만 남겼습니다.
  - pause = 지금까지 들린 위치를 기억하고 엔진 큐를 `clear`(즉시 정적), resume = 그 위치를
    0.12초 되감아 남은 오디오를 다시 보냅니다(음절 유실보다 짧은 반복이 낫습니다).
  - 엔진이 `clear` 에 대기 중인 mark 를 즉시 되돌려 주는 것을 "재생 완료" 로 읽지 않도록
    mark 에 세대를 박았습니다.
  - **기본은 꺼짐**(`audio_pause=False`) — 켜면 끼어들기 동작이 달라지므로 호출자가 정합니다.

## 0.51.0 (2026-09-06)

### Added
- **브랜드 메시지 자유형.** `template_id` 대신 `free` 에 말풍선을 직접 싣습니다 — 템플릿을
  등록하지 않아도 되고, 종료된 친구톡을 대신하는 방식입니다.
  ```python
  client.messages.create(
      to="01012345678", from_="07052358010",
      brand={"channel_id": channel_id,
             "free": {"chatBubbleType": "TEXT", "content": "신메뉴가 나왔어요."}},
  )
  ```
  ⚠️ `free` 안쪽은 **SDK 가 검사하지 않습니다**(불투명 dict). 말풍선 규격표를 서버에 한 벌만
  두는 것이 의도입니다 — SDK 가 사본을 들면 카카오가 칸을 늘린 날 SDK 가 조용히 깎습니다.
- **`client.kakao.brand_images`** — 자유형에 실을 이미지 업로드·목록(동기·비동기 both).
  받은 `id` 를 `free["imageId"]` 에 넣습니다. 한 번 올려 여러 발송에 재사용합니다.
- 에러 코드 `kakao_brand_body_empty`·`kakao_brand_image_not_found` 등록.

### Changed
- ⚠️ **`BrandSendParam` 이 유니온이 되었습니다** — `BrandTemplateSendParam |
  BrandFreeSendParam`. `template_id` 와 `free` 는 **정확히 하나만** 성립하고, 둘 다 주거나
  둘 다 빼면 **mypy 가 잡습니다**(TypedDict 키 집합이 닫혀 있어서다). 타입을 쓰지 않는
  호출자에게는 `TypeError` 입니다.

  한 TypedDict 로 합쳐 optional 로 두지 않은 이유는, 그러면 `{"channel_id": …}` 만 준 오타가
  타입을 통과해 **운영 트래픽의 런타임 400** 이 되기 때문입니다.

  `BrandSendParam` 을 상속하던 코드는 유니온이라 더 이상 되지 않습니다 — 갈래 하나
  (`BrandTemplateSendParam` / `BrandFreeSendParam`, 둘 다 `clawops.types` 에서
  내보냅니다)를 직접 쓰십시오. minor 로 올린 이유가 이것입니다.

### Fixed
- multipart 요청에서 `Content-Type: application/json` 이 남아 **본문이 파싱되지 않던 것**을
  고쳤습니다. boundary 는 httpx 가 붙이므로 우리가 정하면 안 됩니다.

## 0.50.0 (2026-09-05)

### Added
- **카카오 브랜드 메시지 발송.** 채널을 **추가한 친구**에게 나가는 광고성 메시지입니다.
  ```python
  templates = client.kakao.brand_templates.list(channel_id=channel_id)
  client.messages.create(
      to="01012345678", from_="07052358010",
      brand={"channel_id": channel_id, "template_id": templates.data[0].id,
             "variables": {"고객명": "홍길동"}},
  )
  ```
  문자·알림톡과 **배타적**입니다. 오버로드가 타입체커에서 막고, **타입체커를 쓰지 않아도 `TypeError` 로 먼저 거절**합니다(서버 400 을 기다리지 않습니다).
  - ⚠️ **야간에 보낼 수 없습니다.** 20:50~08:00(KST)은 `422 kakao_brand_night_blocked` 입니다.
  - ⚠️ **대체발송이 없습니다.** `fallback` 을 함께 주면 거절합니다.
- **`client.kakao.brand_templates`** — 브랜드 메시지 템플릿 목록. 알림톡 템플릿과 **다른 표**라 `templates` 가 아닌 이쪽으로 조회합니다. sync·async 모두 지원합니다.
  - ⭐ **검수가 없어** `status`·`dormant`·`sendable` 이 없습니다. 목록에 있으면 곧 보낼 수 있습니다.
  - `content`·`header` 는 **`None` 일 수 있습니다** — 본문이 담기는 자리가 말풍선 유형마다 달라 `TEXT`·`IMAGE`·`WIDE` 만 `content` 가 찹니다.
  - `chat_bubble_type` 은 열린 유니온입니다(카카오가 유형을 늘려도 목록이 살아 있습니다).
- **`messages.list(type="bms")`** — 브랜드 메시지만 골라 봅니다.
- **브랜드 전용 오류 코드를 `ClawOpsErrorCode` 에 등록했습니다** — `kakao_brand_night_blocked` · `kakao_fallback_not_allowed` · `kakao_brand_required` · `kakao_brand_template_not_found`.
  - SDK 는 야간 여부를 **직접 재지 않습니다**(호출자 머신의 시계·타임존에 법적 판정을 얹지 않으려는 것입니다). 그래서 야간 실패에 대해 드릴 수 있는 유일한 도구가 이 코드이고, **하루 11시간 동안 나오며 재시도 스케줄링이 이 분기에 달립니다.**
  ```python
  except BadRequestError as e:
      if e.code == "kakao_brand_night_blocked": schedule_after_8am()
  ```
- 새 타입: `BrandMessageCreateParams` · `BrandSendParam` · `KakaoBrandTemplate` · `BrandBubbleType`.
  `KakaoBrandTemplates` · `AsyncKakaoBrandTemplates` 는 `clawops.resources` 에서도 가져올 수 있습니다.

⚠️ 서버 배포(2026-09-04)가 선행되어야 합니다. 그 전 서버에서는 `Brand` 를 모르는 스펙이라 `400` 입니다.

## 0.49.0 (2026-09-05)

**전사 응답의 닫힌 어휘 둘이 조회를 통째로 던지고 있었습니다.** 이 어휘는 서버가 소유하는데
SDK 가 손으로 복제해 두고 있었고, 값이 하나 늘 때마다 같은 사고가 납니다 — 0.28 `routingType`,
0.47 `type: "ata"` 에 이은 3·4회차입니다.

### Fixed
- **🔴 전사를 조회하면 예외가 나던 것 — 2026-08 이후 전사된 통화 전부.** 서버는 화자를 `speaker_0`·`speaker_1`… 로 줍니다(전환 통화처럼 참여자가 셋 이상이면 그만큼 늘어납니다). 그런데 `TranscriptSegment.speaker` 가 `Literal["CUSTOMER", "AGENT"]` 로 닫혀 있어 `calls.get_transcript()` 가 `APIResponseValidationError` 를 냈습니다. **`segments` 는 리스트라 조각 하나가 응답 전체를 죽입니다.**
  - 옛 전사의 `AGENT`·`CUSTOMER` 도 그대로 받습니다. 새 `TranscriptSpeaker` 를 내보냅니다.
- **🔴 전사가 실패한 이유를 물으면 예외가 나던 것.** `TranscriptStatus.stage` 가 `download`·`runtime`·`trigger` 셋만 알았는데, 전사 파이프라인은 `transcription`·`recover` 도 내보내고 **영구 실패는 예외 객체의 속성을 그대로 싣습니다** — 즉 어휘가 코드로 열려 있습니다. 고객이 가장 답을 필요로 하는 순간에 던지던 자리입니다. 새 `TranscriptStage` 를 내보냅니다.
- **`MessageStatus` 에서 `sending` 을 뺐습니다** — **서버가 한 번도 보낸 적 없는 값**입니다. 어휘를 손으로 관리하면 없는 값이 들어오고 있는 값이 빠진다는 증거라, 이번에 함께 정리했습니다. 열린 유니온이라 서버가 실제로 그 값을 보내도 파싱은 됩니다.

### Added
- `MessageType` 자동완성에 `bms`(카카오 브랜드 메시지). 어휘가 이미 열려 있어 **예외는 나지 않던** 자리라 조회에는 영향이 없습니다 — Node SDK 는 여기가 닫혀 있어 실제로 던졌고(0.37.1 에서 수정), 그래서 그쪽이 급했습니다.

## 0.48.0 (2026-09-03)

### Fixed
- **🔴 `collect_dtmf` 가 수집값을 로그에 평문으로 남기던 것.** 키패드로 받는 값은 카드번호·주민번호일 수 있고 실제로 그런 구성이 있습니다. `secure=True` 를 주면 마스킹됐지만, **도구로 호출할 때는 그 인자가 스키마에 없어** 모델이 줄 방법이 없었습니다 — 즉 에이전트가 받은 키패드 값은 **끄는 수단 없이** 전부 평문으로 남았습니다. 이제 `secure` 와 무관하게 **자릿수만** 남깁니다(`DTMF collected: 4 digits`).
  - `secure` 인자는 호환을 위해 남아 있지만 로깅을 좌우하지 않습니다.
- **🔥 도구를 중복 호출하면 대화가 그 자리에서 멎던 것.** 실시간 모델은 앞 호출이 자리를 기다리는 동안 같은 도구를 한 번 더 냅니다(실통화에서 관측). 그때 던지던 맨 `RuntimeError` 를 도구 래퍼가 `"Error: ..."` 로 감싸 모델에게 돌려주니, 모델은 도구가 망가진 줄 알고 **그 뒤로 다시 부르지 않았습니다.** 그때부터 발신자가 누르는 키는 아무도 받지 않고, 발신자에게는 그냥 먹통입니다.
  - 이제 `DtmfCollectorBusy` 로 갈라 도구 경로에서만 `"(이미 입력을 받는 중입니다. 결과를 기다리세요.)"` 로 옮깁니다. `RuntimeError` 를 상속하므로 기존에 이걸 잡던 코드는 그대로 돕니다.
  - LiveKit 경로와 파이프라인 경로 **둘 다** 고쳤습니다.

### Added
- **`collect_dtmf(max_wait=...)` — 수집 하나의 전체 상한.** `timeout` 은 **자리 사이** 대기라 자리가 들어올 때마다 다시 시작합니다(`<Gather>` 를 비롯한 IVR 관행과 같은 의미). 그래서 상한이 없으면 수집 하나가 최대 `max_digits × timeout` 동안 삽니다 — **11자리·5초면 55초**이고, 그동안 모델은 이 도구에 붙들려 아무 말도 못 합니다.
  - 기본 **30초**. `None` 을 주면 상한 없이 종전대로 돕니다.
  - 상한에 닿으면 그때까지 모인 값으로 확정합니다(버리지 않습니다).

## 0.47.0 (2026-09-02)

### Fixed
- **🔥 알림톡을 조회하면 SDK 가 예외를 던지던 것.** 서버는 카카오 알림톡을 `type: "ata"` 로 주는데 모델이 `"kakao"` 를 기다리고 있었습니다 — `"kakao"` 는 **서버가 한 번도 보낸 적 없는 값**입니다.
  - `messages.get()` 은 `APIResponseValidationError`, `messages.list()` 는 **raw `pydantic.ValidationError`** 를 던졌습니다. 후자는 `ClawOpsError` 를 상속하지 않아 `except ClawOpsError` 로도 잡히지 않았습니다.
  - 목록은 **페이지에 알림톡이 한 건만 섞여도 전체가 실패**했습니다. 즉 콘솔로 알림톡을 한 번이라도 보낸 계정은 문자 조회까지 막혔습니다.
  - 고치는 김에 `Message.type` · `Message.status` 의 닫힌 `Literal` 을 걷어냈습니다. 이 어휘는 서버가 소유하므로 값이 하나 늘 때마다 같은 사고가 반복됩니다. 이제 모르는 값도 그대로 통과합니다(타입체커에겐 `str`, IDE 자동완성은 유지).
- **같은 사고가 남아 있던 목록 전부.** `messages.list()` 만 그랬던 게 아닙니다 — 페이지를 돌려주는 목록 **7종 × sync/async 14곳**이 아이템 검증을 클라이언트의 `try` **밖**에서 돌려 raw `pydantic.ValidationError` 가 그대로 샜습니다. 이제 페이지 봉투와 아이템을 한 곳에서 검증해 전부 `APIResponseValidationError`(= `ClawOpsError`) 로 나옵니다.
  - `auto_paging_iter()` 의 **뒷장**도 같은 경로였습니다. 첫 장만 고치면 순회 도중 SDK 밖의 예외로 끊겨 이미 받아 둔 앞장까지 같이 날아갑니다.
  - 대상: `calls` · `messages` · `kakao.channels` · `kakao.templates` · `assignment_links` · `blocked_recipients` · `webhook_logs`. `numbers` · `sip_credentials` · `sip_endpoints` 는 봉투 모델을 써서 원래 안전했습니다.

### Added
- **카카오 알림톡 발송.** `messages.create()` 가 `kakao` · `fallback` 을 받습니다.
  ```python
  client.messages.create(
      to="01012345678", from_="07052358010",
      kakao={"channel_id": "clx9kak0001", "template_id": "clx9tpl0001",
             "variables": {"고객명": "홍길동"}},
      fallback={"body": "주문이 접수되었습니다."},
  )
  ```
  - 문자와 알림톡은 **오버로드 두 개**로 갈립니다. `body=` 와 `kakao=` 를 같이 주면 타입 에러이고, 타입체커를 쓰지 않아도 요청 전에 `TypeError` 로 거절합니다(서버 400 을 기다리지 않습니다).
  - `fallback` 은 **별도 메시지 1건**으로 문자 단가가 따로 청구됩니다. 생략하면 템플릿 본문을 그대로 문자로 보냅니다.
- **`client.kakao.*` — 채널·템플릿 리소스 7종.** 발송에 필요한 ID 를 콘솔에서 손으로 옮겨 적지 않아도 됩니다.
  ```python
  channels = client.kakao.channels.list(status="connected")
  templates = client.kakao.templates.list(channel_id=channels.data[0].id)
  template = next(t for t in templates if t.sendable)
  ```
  `channels.list/retrieve/request_token/connect/disconnect`, `templates.list`, `channel_categories()`. sync·async 와 `client.accounts("AC…").kakao` 모두 지원합니다.
  - ⚠️ `connect` 는 멱등이지만 **타임아웃 시 재호출하지 마세요** — 이미 연결됐을 수 있습니다. `retrieve()` 로 확인하세요.
  - ⚠️ 연결에 실패해도 **인증번호는 소모됩니다.** 다만 `429`/`503` 은 시도되지 않은 것이라 인증번호가 유효합니다.
  - ⚠️ `disconnect` 는 되돌릴 수 없고 **그 채널의 알림톡 템플릿까지 함께 삭제**됩니다.
- **`err.code` — 실패 사유 분기.** 서버가 주던 `{error, code}` 의 `code` 를 SDK 가 읽지 않아 **한글 메시지를 문자열 비교**해야 했습니다. 같은 상태 코드에 사유가 몰립니다(422 만 해도 수신거부·할당량 초과·템플릿 미승인).
  ```python
  except BadRequestError as e:
      if e.code == "kakao_variable_missing": ...
  ```
- **메시지 목록에 `number` 필터와 `type="ata"`.** 둘 다 서버에는 있는데 SDK 에 없던 것입니다.

### Changed
- `MessageCreateParams` 가 클래스에서 `TextMessageCreateParams | KakaoMessageCreateParams` 유니온 별칭이 됐습니다. `import` 는 그대로지만 `MessageCreateParams(...)` 생성자 용법은 깨집니다.
- `messages.list(status="sending")` 이 타입 에러가 됩니다. 서버 쿼리 검증이 `queued|sent|failed|received` 만 받아 **400 을 내던 조합이라 동작한 적이 없습니다.** 응답의 `status` 에는 그대로 나올 수 있습니다.
- **PR CI 를 세웠습니다** — pytest · mypy(REST 표면) · 3.9 import. 이 레포에는 PR 에서 도는 검사가 하나도 없었고, `[tool.mypy] strict = true` 는 실행된 적이 없었습니다.

### Fixed (internal)
- 전체 스위트에서 100% 실패하던 `test_barge_in_truncates_history` 를 고쳤습니다. 오염이 아니라 타이밍이었습니다 — 끊기 전 대기가 0.3초, 단어 하나의 절반이라 전달된 단어가 0이냐 1이냐가 스케줄러에 좌우됐습니다. **이 실패가 publish 게이트를 막고 있었습니다.**

## 0.46.2 (2026-08-24)

### Changed
- **저장소가 [`clawopshq/sdk-python`](https://github.com/clawopshq/sdk-python) 으로 옮겨졌습니다.** 패키지 이름(`clawops`)도, API 도 그대로입니다 — 설치 명령과 코드에 바꿀 것은 없습니다. 구 주소(`learners-superpumped/clawops-python`)는 GitHub 리다이렉트로 계속 열립니다.
- SSL 인증서 검증 실패 시 안내하던 troubleshooting 링크도 새 주소로 맞췄습니다.

## 0.46.1 (2026-08-19)

### Fixed
- **AI 음성이 발화 도중 짧게 끊기던 것.** 세그먼트가 끝날 때 남는 자투리(160바이트 미만)를 SDK 가 **무음으로 채워** 프레임을 맞추고 있었습니다. 세그먼트가 문장 끝이면 무해하지만 **말 도중에 끊기면 그 자리에 0~19ms 짜리 구멍**이 박힙니다.
  - 실통화 녹음 실측(2026-08-19): 발화 중 갭의 끝 위치가 **11/11 · 27/27 모두 `mod 160 = 0`**, 길이는 전부 160 미만, 경계 진폭 3132 — 우연히 정렬될 수 없는 지문이었습니다.
  - 이제 자투리를 **그대로** 보냅니다. 「뒤에 오디오가 더 오는가」는 큐를 들고 있는 서버만 아는 정보라, 프레이밍 판단을 그쪽으로 넘깁니다. 서버는 뒤따르는 mark 를 세그먼트 끝 신호로 읽어 그 자리에서 채웁니다.
  - 파이프라인 경로(`_pipeline_session`)는 **delta 마다** 잘라 채우고 있어 빈도가 더 높았습니다. 이쪽도 원문 그대로 보냅니다.
  - ⚠️ **서버 배포가 선행되어야 합니다.** 자투리를 받아 처리하는 서버(2026-08-19 배포)가 아니면 짧은 RTP 패킷이 그대로 송출됩니다. 되돌려야 하면 `CLAWOPS_TAIL_PAD=1` 로 종전 동작을 켤 수 있습니다.

## 0.46.0 (2026-08-15)

### Added
- **`CallSession.ended_duration` — 서버가 확정한 통화 시간.** 종료 이벤트가 실어 보내던 값을 지금까지 SDK 가 읽지 않아, 통화 기록을 자체 시스템에 적재하려면 REST 를 다시 조회하거나 로컬 시계로 잰 값을 써야 했습니다. 이제 종료 이벤트 하나로 기록을 마칠 수 있습니다.
  ```python
  @agent.on("call_end")
  async def on_end(call):
      print(call.ended_status, call.ended_duration)   # completed 91
  ```
  - `duration` 은 그대로 둡니다 — 그쪽은 **SDK 가 로컬 시계로 재는 경과 시간**이라 통화 중에도 읽히고, 세션이 붙기 전후의 오차를 포함합니다. 기록·정산에 쓸 값은 `ended_duration` 입니다.
  - `call_end` 핸들러 안에서 읽을 수 있습니다. 서버는 미디어 스트림을 먼저 닫고 정리를 마친 뒤에 종료 정보를 보내므로, SDK 가 그 프레임을 **짧게 기다렸다가** `call_end` 를 발화합니다(최대 2초). 정상적인 통화에서는 밀리초 안에 끝나고, 제어 연결이 끊긴 경우에만 상한을 씁니다.
  - 서버가 값을 보내지 않으면 `None` 을 유지합니다. **서버 배포가 선행되어야** 실제 값이 들어옵니다 — 그 전까지는 `None` 입니다.

## 0.45.1 (2026-08-14)

### Fixed
- **LiveKit 을 쓰면 `caller_id_mode` 가 없던 것.** 0.45.0 이 `transfer()` 와 내장 도구 스키마에는 넣었는데, LiveKit 경로는 `transfer_call` 을 따로 정의하고 있어 그쪽만 빠졌습니다. LiveKit 사용자는 AI 가 전환 발신번호를 고를 수 없었습니다.
- 잘못된 `caller_id_mode` 값을 LiveKit 도구에서도 전환을 걸기 **전에** 거절합니다(다른 인자와 같은 규칙).

### Changed
- 개발용 의존성(`clawops[dev]`)에 `livekit-agents` 를 추가했습니다. 없으면 `tests/agent/test_livekit_*.py` 43개가 **항상 건너뛰어져** LiveKit 표면이 검증되지 않은 채 초록불로 보입니다 — 위 결함이 그렇게 새어 나갔습니다.

## 0.45.0 (2026-08-14)

### Added
- **`transfer(caller_id_mode=...)` — 전환받는 쪽에 표시될 번호를 고릅니다.** 지금까지 전환은 **계정 보유번호**(인바운드면 착신 070)로 고정이었고, 원 발신자 번호를 보이게 하려면 `caller_id` 에 번호를 직접 넘기는 수밖에 없었습니다.
  ```python
  await call.transfer("021234567", caller_id_mode="original")   # 환자 번호가 데스크에 표시
  ```
  - `"original"` 은 **선호**입니다. 승계할 수 없는 통화(통신사 직결 인바운드가 아니거나 국내 번호로 정규화되지 않는 발신번호)면 조용히 계정 번호로 내려앉고 **전환은 그대로 성사됩니다**.
  - `caller_id` 로 번호를 직접 주는 것은 **지시**라 성격이 다릅니다. 허용 범위(계정 보유번호 또는 그 통화의 원 발신자)를 벗어나면 전환 자체가 실패합니다. 원 발신자 번호를 직접 넘기던 코드는 승계 불가 통화에서 전환이 실패했는데, `caller_id_mode` 로 바꾸면 그 경우에도 연결됩니다.
  - 둘 다 주면 `caller_id` 가 이기고 `caller_id_mode` 는 무시됩니다.
  - 내장 `transfer_call` 도구에도 `caller_id_mode` 가 추가되어 AI 가 번호 대신 의도를 고를 수 있습니다.
  - 기본 동작은 바뀌지 않습니다. 지정하지 않으면 지금까지와 똑같이 계정 번호가 표시됩니다.

### Fixed
- 내장 `transfer_call` 도구가 건 전환이 실패했을 때 아무 흔적도 남지 않던 것. 결과를 기다리지 않는 구조라 예외가 태스크 안에 갇혔습니다. 이제 로그에 남습니다.

## 0.44.0 (2026-08-14)

### Added
- **`session_factory=` — 한 프로세스로 동시 통화를 받습니다.** 통화마다 세션을 새로 만들어 대화 이력·오디오 경로·진행 중인 작업이 그 통화 안에 갇힙니다. 번호를 여러 개 운영하더라도 번호별로 프로세스를 나눌 필요가 없습니다.
  ```python
  agent = ClawOpsAgent(
      from_="07012341234",
      session_factory=lambda: OpenAIRealtime(system_prompt="..."),
  )
  ```
  LiveKit 은 `session_factory=lambda: LiveKitSession(create)` 로 넘깁니다. HTTP 플러그인용 `http_context` 도 통화별로 열립니다.

### Fixed
- **동시 통화가 서로를 덮어쓰던 것.** `session=` 으로 넘긴 객체를 모든 통화가 공유했습니다. 두 번째 통화가 시작되면 첫 통화의 **대화 이력이 초기화되고 음성이 두 번째 통화 쪽으로 나갔으며**, 첫 통화가 끝날 때의 정리가 두 번째 통화를 내렸습니다. `session_factory=` 로 바꾸면 해결됩니다. `session=` 은 그대로 동작하지만 동시 통화 1건까지만 안전하며, 두 번째 통화가 시작되면 원인을 지목하는 에러 로그를 남깁니다.
- **실패한 발신의 정리가 다른 통화를 끊던 것.** 응답 전에 실패한 통화를 정리하면서 공유 세션을 종료해, 그때 진행 중이던 다른 통화가 무음이 됐습니다.

### Changed
- `session` 과 `session_factory` 중 **정확히 하나**를 지정해야 합니다. 둘 다 없거나 둘 다 있으면 `AgentError` 입니다(예전에는 `session` 이 필수 인자라 `TypeError` 였습니다).

## 0.43.1 (2026-08-14)

### Fixed
- **키패드 입력이 통화 사이에서 섞이던 것.** `collect_dtmf()` 로 받지 않는 입력(=AI 가 알아서 듣는 키패드)의 버퍼가 통화가 아니라 `ClawOpsAgent` 인스턴스에 붙어 있었습니다. 한 프로세스가 통화 두 건을 처리할 때 두 발신자가 0.5초 안에 키를 누르면 **입력이 한 덩어리로 합쳐지고, 그 덩어리가 나중에 누른 통화에만** 전달됐습니다 — 다른 통화는 입력을 통째로 잃습니다. 이제 통화마다 자기 버퍼를 씁니다.
- **주입 중이던 입력이 다음 키에 잘리던 것.** 앞서 누른 숫자를 AI 에게 전달하는 중에 다음 키를 누르면 그 전달이 취소돼 앞 숫자가 사라졌습니다. 통화가 한 건일 때도 나던 문제입니다.
- 통화가 끝난 뒤 뒤늦게 깨어난 입력 처리가 아무 일도 하지 않습니다.

## 0.43.0 (2026-08-14)

### Fixed
- **긴 전환 통화가 제어 연결을 죽이던 것 — 업그레이드를 권합니다.** `transfer_call()` 로 넘긴 통화가 40초를 넘기면 대기가 취소되고, 뒤늦게 도착한 완료 이벤트가 그 대기를 건드려 예외를 던졌습니다. 그 예외가 수신 루프 밖으로 새어 **제어 연결 태스크가 죽고 재접속이 일어나지 않았습니다** — 이후 걸려오는 전화가 전부 폴백으로 넘어갑니다(실제 사고: 16시간). 병원 데스크 상담처럼 전환 통화가 1분을 넘는 것은 흔한 일이라, 전환을 쓰신다면 사실상 상시 노출돼 있었습니다.
  - `timeout` 은 이제 **대상이 전화를 받기까지**의 대기에만 적용됩니다(문서가 원래 설명하던 동작). 받은 뒤의 통화가 얼마나 길어지든 `transfer_call()` 은 전환이 끝날 때까지 기다렸다가 결과를 돌려줍니다.
  - 이벤트 처리 중 예외가 나도 제어 연결이 유지되고, 예외 종류와 무관하게 재접속합니다. 예전에는 일부 예외만 재접속 대상이었습니다.
  - 결과가 중복·지연 도착해도 예외가 나지 않습니다.
- **같은 통화에서 전환을 다시 걸면 이전 대기를 잃던 것.** `after_transfer="return"` 은 전환이 끝나면 AI 로 돌아오므로 한 통화에서 여러 번 전환할 수 있는데, 2차 요청이 1차 대기를 덮어써 1차 `transfer_call()` 이 영영 반환되지 않았습니다. 이제 요청마다 상관 ID 를 붙여 각자 자기 결과를 받습니다.
  - 서버가 이 ID 를 되돌려야 하므로 **서버 배포가 선행되어야** 합니다. 아직이면 통화 단위 폴백으로 동작합니다(그 동안에는 한 통화에 전환 하나만 구분 가능).
- 통화가 먼저 끝나면 남은 전환 대기를 정리합니다. 종료 통지와 전환 결과는 서로 다른 요청으로 전달되어 도착 순서가 뒤바뀔 수 있으므로, 짧은 유예를 두고 정리해 정상 결과를 버리지 않습니다.

## 0.42.0 (2026-08-14)

### Added
- **`calls.create(agent_id=)` — 매니지드 에이전트로 발신.** 콘솔에서 만든 AI 에이전트에게 아웃바운드 통화를 맡긴다. REST 는 `AgentId` 를 계속 지원해 왔는데 SDK 에만 파라미터가 없어, 0.38.0 에서 AI Completion 모드를 걷어낸 뒤로 **SDK 로 AI 통화를 거는 방법이 `url=`(VoiceML 서버 직접 구현)뿐**이었다. 그 공백을 메운다.
  - `call_context={"instruction": ..., "variables": {...}}` — **이번 통화에만** 적용되는 지시. 에이전트 자체 설정은 그대로 두고 이 통화만 다르게 행동시킨다. 같은 에이전트로 동시에 거는 다른 통화에는 영향이 없다. 파라미터는 snake_case 로 받고 본문은 PascalCase 로 보낸다(스펙이 `additionalProperties: false` 라 snake_case 를 그대로 흘리면 400).
- **`calls.create(call_flow_id=, variables=)` — 콜 플로우로 발신.** 콘솔 빌더로 만든 결정적 ARS 플로우가 통화를 진행한다. `variables` 는 멘트·URL·본문의 `{{이름}}` 을 치환하며 `call_flow_id` 와 함께일 때만 쓸 수 있다(단독 지정 시 400). `caller`·`callee`·`recording_url`·`recording_duration`·`http_status` 는 통화 중 자동으로 채워지는 예약 변수라 지정할 수 없다.
- `url`·`agent_id`·`call_flow_id` 는 서로 배타적이고, **셋 다 생략하면 Agent SDK 모드**로 From 번호에 연결된 세션이 받는다.

### Fixed
- `CallCreateParams` 에 `machine_detection` 이 빠져 있던 것. 메서드 시그니처에는 있었으나 TypedDict 에 없어 타입 힌트만 어긋나 있었다.

## 0.40.0 (2026-07-31)

### Added
- **수신거부(DNC) 명단 리소스 — `client.blocked_recipients`.** 광고 문자 하단의 080 무료수신거부, ARS 의 "수신거부 9번", 상담 중 구두 요청 등으로 접수된 번호를 계정 단위로 관리한다. 등록된 번호는 그 계정의 **발신**(전화·문자)에서 제외되며 **착신은 막지 않는다** — 수신거부 접수 자체가 우리 080/ARS 로 오는 착신이기 때문이다.
  - `create(number=, channel=)` — 하이픈·`+82` 표기 모두 허용되며 국내 표기로 정규화되어 저장된다. **멱등**이라 이미 차단 중인 (번호, 채널)을 다시 등록해도 에러가 아니라 기존 항목을 돌려준다(같은 사람이 수신거부를 두 번 요청하는 것은 정상 상황이다).
  - `list(channel=, number=, status=)` — 기본은 차단 중인 것만. `status="released"|"all"` 로 해제 이력까지 조회. `number` 는 하이픈 표기로 넣어도 정규화 후 대조한다.
  - `retrieve(block_id)` / `update(block_id, note=)` — 메모만 수정한다. 번호·채널은 바꿀 수 없다(증빙이 뒤틀린다).
  - `release(block_id)` — 해제. **항목을 삭제하지 않고** `active=False` + `unblocked_at` 을 기록해 이력으로 남긴다. 언제 거부했고 언제 풀렸는지가 곧 증빙이라서다. 재호출해도 최초 해제 시각은 덮지 않는다.
  - 전화와 문자는 각각 따로 차단한다. 같은 번호라도 채널마다 별개 항목이라 둘 다 막으려면 `channel` 을 바꿔 두 번 등록한다.
- 내부: `_base_client` 에 `_patch` / `_delete_with_response` 추가(sync·async). 후자는 soft delete 처럼 삭제 결과 리소스를 그대로 반환하는 endpoint 용으로, 응답을 버리는 기존 `_delete` 는 그대로 둔다.

## 0.38.0 (2026-07-23)

### Removed
- **`calls.create(ai=...)` — AI Completion 모드 제거.** 서버에서 해당 모드가 종료되어 `AI` 필드를 포함한 요청은 이제 `410 ai_mode_removed` 로 거절된다. `ai` 파라미터와 `AIConfigParam`/`OpenAIAIConfigParam`/`GeminiAIConfigParam` 타입을 삭제했다. 통화에 AI 를 태우려면 **Agent SDK**(`clawops.agent`) 를 쓰거나, 콘솔에서 만든 매니지드 에이전트 또는 VoiceML(`url=`) 을 사용한다.

## 0.37.1 (2026-07-23)

### Fixed
- **발신 통화에서 `@agent.tool` 로 등록한 도구가 AI 에게 전달되지 않던 문제.** 발신은 originate 직후 prewarm 이 돌면서 LLM 에 tool 스키마를 확정 전송하는데(OpenAI `session.update` / Gemini Live connect config), 도구 주입은 상대가 받은 뒤인 `_start_call_session` 에서야 실행됐다. 즉 **유저 도구가 통째로 빠진 채 세션이 시작**되어, 아무리 유도해도 도구가 호출되지 않았다. 착신·`PipelineSession`·`LiveKitSession` 은 영향 없음. 이제 prewarm 전에 도구를 주입한다.
- MCP 도구는 통화 시작 시점에야 registry 에 붙으므로 prewarm 스키마에 없었다. OpenAI Realtime 은 `attach()` 에서 도구가 바뀐 경우에만 `session.update` 로 재전송한다. Gemini Live 는 연결 후 도구 변경이 불가능하므로, MCP 서버가 설정돼 있으면 prewarm 을 건너뛰고 기존 `start()` 경로로 간다.
- **prewarm 창(상대가 받기 전)에 내장 통화 제어 도구가 호출되면 크래시**했다. 이 구간의 통화 객체는 버퍼링 stub 이라 `hangup()` 이 없어 `AttributeError` 가 나고, tool 결과가 모델에 영영 돌아가지 않아 응답이 멈춘 채로 통화가 시작될 수 있었다. 이제 "통화가 아직 연결되지 않았습니다" 결과를 돌려줘 모델이 응답 후 다시 호출할 수 있다.

## 0.37.0 (2026-07-22)

### Fixed
- **발신 결과가 통보되지 않던 문제.** 서버는 `call.ended` 에 종료 사유를 `status` 로 싣지만 `_handle_ended` 가 이 값을 버리고 `_mark_ended()` 가 항상 `"completed"` 를 하드코딩해, **상대가 받지 않은 통화(무응답)가 성사된 통화와 구분되지 않았다.** `await session.wait()` 가 조용히 리턴하고 `status` 도 `completed` 라서 발신 실패를 코드로 감지할 방법이 아예 없었다. 이제 서버가 통보한 종료 사유(`completed` / `no-answer` / `busy` / `rejected` / `canceled` / `failed`)를 그대로 반영한다.
- `Call.status` 의 `Literal` 이 `queued`/`ringing`/`in-progress`/`completed`/`failed` 5종만 허용해, 정작 진단이 필요한 **무응답·통화중·거절 통화를 `client.calls.get()` 으로 조회하면 `ValidationError` 로 실패**했다. 서버가 실제로 반환하는 9종 전부를 허용하도록 넓혔다.

### Added
- `CallSession.ended_status` — 서버가 통보한 최종 종료 사유. 통화가 끝나기 전에는 `None`. `completed` 만이 실제로 연결된 통화를 의미한다.
- `call_failed` 이벤트가 실제로 발화된다. 통화가 **연결되지 못하고** 끝났을 때 `(call, reason)` 으로 호출되며 `reason` 은 `ended_status` 와 같다. 이전에는 서버가 보내지 않는 `call.failed` 에만 묶여 있어 영원히 호출되지 않는 죽은 API 였다. 이제 발신 한 건은 반드시 `call_start`+`call_end`(연결됨) 또는 `call_failed`(미연결) 중 한쪽으로 끝난다.

## 0.36.0 (2026-07-18)

### Added
- `MediaWebSocket` 와 `CallSession` 을 `clawops.agent` 의 public export 로 노출한다. mediaUrl 을 직접 받아 통화 하나를 굴리는 **멀티테넌트 서버 러너**(통화당 설정을 디스패치하는 워커)가 Control WS 없이 이 부품들을 조립할 수 있다. Node SDK(`@teamlearners/clawops/agent`)가 동일 부품을 열어둔 것과 대칭.
- `CallSession.bind_transport(...)` — 오디오/DTMF/hangup transport 배선을 캡슐화하는 public 메서드. 서버가 이미 열린 미디어 transport 로부터 통화를 구동할 때 `_send_audio_fn`/`_media_ws` 같은 내부 필드를 직접 대입하지 않아도 된다. 기존 `ClawOpsAgent` 경로도 이 메서드를 사용하도록 정리(동작 불변).

## 0.35.0 (2026-07-16)

### Added
- LiveKit transport 에서 `@agent.on("transcript")` 훅 지원. LiveKit `AgentSession` 의 최종 대화 항목(`conversation_item_added`)을 네이티브 세션과 동일하게 `transcript` 이벤트(`role`, `text`)로 흘려보낸다. 세션만 `LiveKitSession` 으로 바꿔도 트랜스크립트를 모아 후처리(요약·escalation 등)하던 기존 앱이 그대로 동작한다.
- 예제(`examples/livekit_agent.py`)에 xAI TTS(`livekit-plugins-xai`) 분기 추가 — `XAI_API_KEY` 가 있으면 OpenAI Realtime(text) + xAI TTS 조합으로 음색을 낸다. `voice`/`language` 는 문자열로 지정한다(예: `voice="iris"`, `language="ko"`).

## 0.34.0 (2026-07-16)

### Added
- `clawops[livekit]` extra 신설 — LiveKit Agents 로 작성한 에이전트를 LiveKit 서버·SIP 없이 ClawOps 전화망에서 실행한다(`LiveKitSession`). 관용적인 LiveKit 코드(`Agent` 서브클래스·`AgentSession(llm=,tts=,stt=,...)`·`@function_tool`)를 그대로 쓰고, `session.start(room=...)` 의 `room` 만 우리가 대신 처리한다. OpenAI Realtime(`modalities=["text"]`) + Cartesia/xAI 등 TTS 조합으로 음색을 교체할 수 있다. 착신(`serve()`)·발신(`call(to=)`)·prewarm·내장 전화 도구(hang_up/transfer/collect_dtmf/send_dtmf)·`@agent.tool` 브리지 지원. 기존 `OpenAIRealtime`/`GeminiRealtime`/`PipelineSession` 경로는 변경 없음. Python 전용 extra (`agent-all` 에는 미포함).

## 0.33.0 (2026-07-08)

### Added
- `ClawOpsAgent(machine_detection=...)` — 인스턴스 레벨 AMD default. 생성자에서 지정하면 해당 에이전트의 모든 발신에 적용된다(`"Enable"` / `"Hangup"` / `None`). `agent.call(to, machine_detection=...)` 의 호출별 override 는 그대로 유지되며, 우선순위는 **호출 인자 > 인스턴스 default > 비활성**. per-call `machine_detection`(0.29.0)의 편의 확장이며 서버 동작 변화는 없다.

## 0.32.0 (2026-07-07)

### Added
- `call.transfer(destination_type=...)` + `transfer_call` 도구에 `destination_type`(`pstn`/`sip`) 파라미터 추가. `'sip'` 이면 `to` 를 SIP URI(`sip:user@host`)로 해석해 통화를 PSTN carrier 없이 SIP 엔드포인트로 직접 전환한다(INVITE 브릿지 — 녹음·관측 유지). 기본값 `'pstn'` (기존 전화번호 전환과 하위호환). `'sip'` 전환은 `sip_trunk` 부가서비스가 필요하며, 미보유 시 전환은 실패하고 통화는 AI 로 유지된다.

## 0.31.0 (2026-06-22)

### Added
- `numbers.update` 에 인바운드 라우팅 파라미터 추가 — `routing_type`(`webhook`/`sip`/`softphone`), `sip_endpoint_id`, `sip_credential_id`. `softphone` 으로 등록된 SIP 단말 착신, `sip` 으로 외부 PBX 라우팅을 API 로 설정할 수 있다 (둘 다 `sip_trunk` 부가서비스 필요).
- `sip_credentials` / `sip_endpoints` 조회 전용 리소스 신설 (`list` / `get`, sync·async) — softphone/sip 라우팅 설정에 필요한 id 를 조회한다. 평문 password·ha1 은 응답에 포함되지 않는다.
- `PhoneNumber` 응답 모델에 `routing_type` / `sip_endpoint_id` / `sip_credential_id` 필드 추가.

## 0.30.0 (2026-06-10)

### Added
- `Call.answered_by` — AMD(`machine_detection`) 결과 필드 추가. `machine_detection` 을 켠 발신 통화에서 `human`(사람) / `machine`(자동응답기·음성사서함) / `unknown`(판정 불가) 값으로 채워진다 (`calls.get` / `calls.list` 응답). 미사용 통화는 `None`.
- README·agent quickstart 에 `machine_detection` 사용법과 `answered_by` / status callback `AnsweredBy` 확인 방법 문서화.

## 0.27.1 (2026-05-26)

### Fixed
- `clawops[openai]` extra 가 `openai>=2.0.0` 만 설치하여 OpenAI Realtime 사용 시 `You need to install openai[realtime]` 오류가 발생하던 문제 수정 — extra 를 `openai[realtime]>=2.0.0` 로 변경하여 websocket 전송 의존성을 함께 설치한다.

## 0.27.0 (2026-05-26)

### Added
- **Outbound realtime prewarm** — Realtime 세션(OpenAI / Gemini)을 발신(originate) 직후 ring 구간에 미리 연결하고 greeting 오디오를 prebuffer 하여, 상대가 받는 즉시 첫 음성을 송출한다. `answer → first-audio` 지연이 약 2.6s → ~0ms(prebuffer 즉시 flush) 수준으로 단축된다.
  - `ClawOpsAgent(prewarm_enabled=True)` (기본값) 로 통화 단위 on/off.
  - prewarm 트리거 우선순위: `agent.call()` originate 직후(주 경로) → `call.ringing`(fallback) → `call.outbound_ready`(최종 fallback). `call.ringing` 은 트렁크가 SIP 18x 를 올리지 않으면 도착하지 않을 수 있어 신뢰하지 않는다.
  - `[PREWARM-T]` 로그 마커(start / done / attach / first-audio)로 latency 측정. `scripts/measure_prewarm_cost.py` 로 A/B 측정.

### Fixed
- prewarm 후 attach 전에 통화가 실패/종료될 때 LLM WebSocket 연결을 `session.stop()` 으로 정리하여 leak 을 방지한다 (`_prewarm_attached` 가드로 정상 통화의 이중 stop 방지). originate-time prewarm 으로 미응답/거절 통화에서도 prewarm 연결이 열리므로 필수.
- OpenAI / Gemini Realtime `stop()` 이 receive loop task 를 cancel 후 `asyncio.gather(..., return_exceptions=True)` 로 수거하여 "Task exception was never retrieved" 경고를 제거한다 (현재 실행 중인 task 는 self-await 회피를 위해 제외).

### Known limitations
- `ClawOpsAgent` 1 인스턴스 = 동시 outbound 통화 1건 가정. 단일 공유 세션이므로 동시 다발 발신(같은 인스턴스)은 미지원.
