"""타입 수준 회귀 — 이 디렉터리의 검사 기법 자체가 살아 있는지 고정한다."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing_extensions import assert_type

    from clawops import AsyncClawOps, ClawOps
    from clawops.agent import CallSession, ClawOpsAgent, ClawOpsAgentOptions, Session
    from clawops.types import Call, CallCreateParams, CallerIdMode, Shared010CallCreateParams

    # `to` 는 Required 다. 빠지면 타입 에러여야 한다.
    _missing_required: CallCreateParams = {  # type: ignore[typeddict-item]
        "from_": "07052358010",
        "url": "https://example.com/twiml",
    }

    # TypedDict 는 키 집합이 닫혀 있다. 없는 키는 타입 에러여야 한다.
    _unknown_key: CallCreateParams = {
        "to": "01012345678",
        "from_": "07052358010",
        "nope": 1,  # type: ignore[typeddict-unknown-key]
    }

    def _sync_calls(client: ClawOps, number: str, maybe_number: str | None, mode: CallerIdMode | None) -> None:
        to = "01012345678"
        ordinary: CallCreateParams = {"to": to, "from_": number}
        shared: Shared010CallCreateParams = {
            "to": to, "sdk_agent_id": "support", "caller_id_mode": "shared-010"
        }
        assert_type(client.calls.create(**ordinary), Call)
        assert_type(client.calls.create(**shared), Call)
        assert_type(client.calls.create(to=to, from_=number, caller_id_mode=mode), Call)
        assert_type(
            client.calls.create(to=to, caller_id_mode="shared-010", from_=maybe_number, agent_id="managed"), Call
        )
        assert_type(client.calls.create(to=to, caller_id_mode="shared-010", url="https://example.com/voice"), Call)
        assert_type(client.calls.create(to=to, caller_id_mode="shared-010", call_flow_id="main"), Call)
        client.calls.create(to=to)  # type: ignore[call-overload]
        client.calls.create(to=to, caller_id_mode="shared-010")  # type: ignore[call-overload]
        client.calls.create(to=to, sdk_agent_id="support")  # type: ignore[call-overload]
        client.calls.create(to=to, from_=number, caller_id_mode="pool")  # type: ignore[call-overload]
        client.calls.create(  # type: ignore[call-overload]
            to=to, from_=number, sdk_agent_id="support", caller_id_mode="shared-010"
        )
        client.calls.create(  # type: ignore[call-overload]
            to=to, sdk_agent_id="support", agent_id="managed", caller_id_mode="shared-010"
        )

    async def _async_calls(client: AsyncClawOps, number: str) -> None:
        to = "01012345678"
        assert_type(await client.calls.create(to=to, from_=number), Call)
        assert_type(await client.calls.create(to=to, caller_id_mode="shared-010", sdk_agent_id="support"), Call)
        assert_type(await client.calls.create(to=to, caller_id_mode="shared-010", agent_id="managed"), Call)
        await client.calls.create(to=to, caller_id_mode="shared-010")  # type: ignore[call-overload]
        await client.calls.create(  # type: ignore[call-overload]
            to=to, caller_id_mode="shared-010", url="https://example.com/voice", agent_id="managed"
        )
        await client.calls.create(  # type: ignore[call-overload]
            to=to, caller_id_mode="shared-010", sdk_agent_id="support", url="https://example.com/voice"
        )

    async def _sdk_agent(session: Session) -> None:
        to = "01012345678"
        options: ClawOpsAgentOptions = {"sdk_agent_id": "support", "session": session}
        numberless = ClawOpsAgent(**options)
        numbered = ClawOpsAgent(from_="07012345678", session_factory=lambda: session, prewarm_enabled=False)
        assert_type(await numberless.call(to), CallSession)
        assert_type(await numbered.call(to, caller_id_mode="shared-010"), CallSession)
        ClawOpsAgent(session=session)  # type: ignore[call-overload]
        ClawOpsAgent(from_="07012345678", sdk_agent_id="support", session=session)  # type: ignore[call-overload]
        await numbered.call(to, caller_id_mode="pool")  # type: ignore[arg-type]
