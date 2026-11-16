# 트러블슈팅

## 연결이 거절됨 (AgentAuthRejectedError)

### 증상

에이전트가 시작하자마자, 또는 실행 중에 아래 오류로 멈춥니다.

```
Control WS 거절(403 api_key_invalid) — 재연결하지 않는다: API 키가 유효하지 않습니다(폐기·만료됐거나 다른 계정의 키). ...
```

### 원인과 해결

다시 연결해도 결과가 같은 거절이라 SDK 가 재연결을 멈춥니다. `err.code` 로 원인을 구분합니다.

| `code` | 원인 | 해결 |
| --- | --- | --- |
| `api_key_invalid` | API 키가 폐기·만료됐거나 다른 계정의 키 | 콘솔에서 새 키를 발급해 `CLAWOPS_API_KEY` 에 넣고 에이전트를 다시 띄웁니다 |
| `api_key_missing` | API 키가 전달되지 않음 | `api_key` 인자나 `CLAWOPS_API_KEY` 를 설정합니다 |
| `number_not_owned` | 번호가 이 계정 것이 아님(반납·회수 등) | 콘솔에서 계정의 번호를 확인하고 `from_` 을 고칩니다 |
| `number_missing` | 번호가 전달되지 않음 | `from_` 을 설정합니다 |

```python
from clawops.agent import AgentAuthRejectedError

try:
    await agent.serve()
except AgentAuthRejectedError as err:
    print(err.code, err.message)
```

## SSL 인증서 에러 (SSLCertVerificationError)

### 증상

서버 연결 시 아래와 같은 에러가 반복 출력됩니다.

```
Control WS error: Cannot connect to host api.koreaaitel.com:443 ssl:True [SSLCertVerificationError: (1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1028)')]
```

### 원인

Python이 시스템의 CA(인증 기관) 루트 인증서를 찾지 못해 SSL 인증서 체인을 검증할 수 없을 때 발생합니다. 주로 다음 환경에서 나타납니다.

| 환경                     | 원인                                         |
| ------------------------ | -------------------------------------------- |
| macOS + python.org 설치  | 설치 후 인증서 설정 스크립트를 실행하지 않음 |
| conda / pyenv / 가상환경 | 시스템 인증서 저장소를 상속하지 못함         |
| Docker 컨테이너          | `ca-certificates` 패키지 미설치              |
| 기업 네트워크            | 프록시/방화벽이 자체 CA 인증서를 사용        |

### 해결 방법

#### 방법 1: certifi 설치 (권장)

`certifi` 패키지를 설치하면 aiohttp가 자동으로 감지하여 번들된 CA 인증서를 사용합니다.

```bash
pip install --upgrade certifi
```

#### 방법 2: macOS 인증서 설치

python.org에서 Python을 설치한 경우, 인증서 설치 스크립트를 실행합니다.

```bash
# Python 버전에 맞게 경로를 수정하세요
/Applications/Python\ 3.12/Install\ Certificates.command
```

#### 방법 3: 환경변수로 인증서 경로 지정

```bash
# certifi가 설치된 경우
export SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())")

# 또는 시스템 인증서 경로를 직접 지정
export SSL_CERT_FILE=/path/to/ca-bundle.crt
```

#### 방법 4: Docker 환경

Dockerfile에 CA 인증서 패키지를 추가합니다.

```dockerfile
# Debian/Ubuntu 기반
RUN apt-get update && apt-get install -y ca-certificates

# Alpine 기반
RUN apk add --no-cache ca-certificates
```

### 확인 방법

Python에서 현재 SSL 인증서 상태를 확인할 수 있습니다.

```python
import ssl
import certifi

# 시스템 기본 인증서 경로
print(ssl.get_default_verify_paths())

# certifi 인증서 경로
print(certifi.where())
```

---

## WebSocket 연결 실패

### 증상

`AgentConnectionError`가 발생하거나 `Control WS reconnecting...` 로그가 반복됩니다.

### 확인 사항

| 항목          | 확인 방법                                                                     |
| ------------- | ----------------------------------------------------------------------------- |
| API 키        | `CLAWOPS_API_KEY`가 `sk_`로 시작하는 유효한 키인지 확인                       |
| 계정 ID       | `CLAWOPS_ACCOUNT_ID`가 `AC`로 시작하는 유효한 ID인지 확인                     |
| 전화번호      | `from_`에 지정한 번호가 계정에 등록된 번호인지 확인                           |
| 네트워크      | `api.koreaaitel.com:443`으로의 아웃바운드 WebSocket(WSS) 연결이 허용되는지 확인 |
| 방화벽/프록시 | 기업 네트워크에서 WebSocket 프로토콜이 차단되지 않는지 확인                   |

---

## 발신했는데 전화가 오지 않습니다

### 증상

`agent.call()` 은 정상적으로 리턴하고 에러도 없는데, 대상 휴대폰에 전화가 오지 않습니다.
대시보드 통화 이력에는 **무응답**으로 남습니다.

### 1단계 — 통화가 통신망까지 갔는지 확인

먼저 종료 사유를 확인합니다. ([발신 결과 확인하기](quickstart.md#발신-결과-확인하기))

```python
session = await agent.call("01012345678", timeout=60)
await session.wait()
print(session.ended_status)
```

| `ended_status` | 해석 |
| --- | --- |
| `no-answer` | **통신망까지 정상 전달되어 벨 신호까지 올라갔으나 응답이 없었음.** 아래 2단계로. |
| `busy` / `rejected` | 상대 단말이 통화중이거나 거절 |
| `failed` | 시스템/네트워크 오류 — 문의 바랍니다 |
| `None` 인 채로 대기 | 발신 자체가 시작되지 않음 — 번호·권한 문제 |

통화 이벤트 조회 API(`GET /v1/accounts/{accountId}/calls/{callId}/events`)로 더 자세한 진행 내역을 볼 수 있습니다.

### 2단계 — `no-answer` 인 경우

`no-answer` 는 **통신망이 상대 단말을 호출(벨)했지만 받지 않았다**는 뜻입니다.
발신 측에는 통화 연결음이 정상적으로 들립니다. 아래를 순서대로 확인하세요.

| 확인 | 방법 |
| --- | --- |
| `timeout` 이 너무 짧지 않은지 | 기본값 `60`. 예제를 따라 `30` 으로 줄였다면 벨이 몇 번 울리기도 전에 취소됩니다 |
| **단말 수신 차단** | 아이폰 "알 수 없는 발신자 무음 처리", 스팸 차단 앱(T전화·후후 등)의 070 자동 차단, 방해금지 모드. **이 경우 발신 측에는 정상적으로 벨 소리가 들리지만 단말은 울리지 않습니다** |
| 다른 단말로 테스트 | 동료·가족 등 **다른 사람의 휴대폰 번호**로 발신해 보세요. 그쪽이 정상이면 원래 단말의 차단 설정 문제입니다 |
| 다른 발신번호로 테스트 | 대시보드에서 번호를 추가 발급받아 다른 `from_` 으로 발신해 보세요. 특정 발신번호가 단말/통신사 스팸 필터에 걸린 경우를 구분할 수 있습니다 |

두 가지를 모두 바꿔가며 교차로 시험하면 원인이 **발신번호 쪽인지 수신 단말 쪽인지** 빠르게 좁혀집니다.

---

## 응답 직후 통화가 끊깁니다 (AGENT_SESSION_INIT_FAILED)

### 증상

상대가 전화를 받은 직후 통화가 끊기고, 통화 이력에 아래 실패 사유가 남습니다.

```
AGENT_SESSION_INIT_FAILED
SDK 의 _active_sessions 에 callId 가 없습니다.
```

### 원인

발신을 요청한 프로세스와, 플랫폼이 통화를 넘겨준 프로세스가 서로 다르기 때문입니다.
플랫폼은 **발신번호 1개당 Agent 연결 1개**만 유지하며, 같은 번호로 새 연결이 들어오면 기존 연결을 끊습니다.

- `agent.serve()` 를 돌리는 프로세스와 발신 스크립트를 **따로** 실행한 경우
- 이전 발신 스크립트가 완전히 종료되지 않은 상태에서 새로 실행한 경우
- `agent.call()` 을 쓰지 않고 통화 생성 REST API 를 직접 호출한 경우

### 해결

수신과 발신을 함께 하려면 **한 프로세스 안에서** `await agent.connect()` 후 `agent.call()` 을 호출하세요.
발신 스크립트를 재실행할 때는 이전 프로세스가 종료됐는지 확인하세요.

```python
await agent.connect()
session = await agent.call("01012345678")   # 같은 프로세스에서 발신
# 이 agent 는 인바운드 수신도 계속 처리
```

---

## 디버그 로깅

문제 원인을 파악하기 어려울 때 디버그 로깅을 활성화하면 상세한 연결 과정을 확인할 수 있습니다.

```python
import logging
logging.getLogger("clawops.agent").setLevel(logging.DEBUG)
```

---

## 도움 요청

위 방법으로 해결되지 않으면 아래 정보를 포함하여 문의해 주세요.

- Python 버전 (`python --version`)
- OS 및 환경 (macOS, Linux, Docker, Windows WSL 등)
- 설치된 패키지 버전 (`pip show clawops aiohttp certifi`)
- 디버그 로그 출력
