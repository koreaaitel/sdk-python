# Tracing (OpenTelemetry)

통화 흐름, 도구 호출, LLM 세션을 OpenTelemetry span으로 추적할 수 있습니다. `opentelemetry-api`가 설치되지 않은 환경에서는 자동으로 no-op 처리됩니다.

## 설치

```bash
pip install "koreaaitel[tracing]"

# + exporter
pip install opentelemetry-sdk opentelemetry-exporter-otlp
```

## 사용법

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)

from clawops.agent import ClawOpsAgent, OpenAIRealtime
from clawops.agent.tracing import TracingConfig

agent = ClawOpsAgent(
    from_="07012341234",
    session=OpenAIRealtime(
        system_prompt="상담원입니다.",
    ),
    tracing=TracingConfig(
        service_name="my-call-center",
        tracer_provider=provider,
    ),
)
```

## TracingConfig

| 옵션 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `enabled` | `bool` | `True` | Tracing 활성화 여부 |
| `service_name` | `str` | `"clawops-agent"` | OTEL 서비스 이름 |
| `tracer_provider` | `TracerProvider \| None` | `None` | 커스텀 TracerProvider |

## Span 구조

```
call (call.id, call.from, call.to, call.duration_ms)
├── mcp.connect (mcp.server.type, mcp.tools.count)
├── llm.session (gen_ai.system, gen_ai.request.model)
│   └── llm.generation
├── tool.call (tool.name, tool.source, tool.duration_ms)
│   └── mcp.call_tool (mcp.tool.name, mcp.tool.is_error)
└── tool.call (tool.name, tool.source)
```

## Span Attribute

| Span | Attribute | 설명 |
|------|-----------|------|
| `call` | `call.id`, `call.from`, `call.to`, `call.duration_ms` | 통화 정보 |
| `mcp.connect` | `mcp.server.type`, `mcp.server.command`, `mcp.server.url`, `mcp.tools.count` | MCP 서버 |
| `llm.session` | `gen_ai.system`, `gen_ai.request.model`, `gen_ai.request.voice` | LLM 세션 |
| `tool.call` | `tool.name`, `tool.source`, `tool.duration_ms` | 도구 호출 |
| `mcp.call_tool` | `mcp.tool.name`, `mcp.tool.is_error` | MCP 도구 |
