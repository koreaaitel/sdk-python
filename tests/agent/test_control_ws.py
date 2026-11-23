# tests/agent/test_control_ws.py
from clawops.agent._control_ws import build_control_ws_url


def test_build_url_https():
    url = build_control_ws_url(
        base_url="https://api.claw-ops.com",
        account_id="AC123",
        number="07012341234",
    )
    assert url == "wss://api.claw-ops.com/v1/accounts/AC123/agent/listen?number=07012341234"


def test_build_url_http():
    url = build_control_ws_url(
        base_url="http://localhost:3000",
        account_id="AC123",
        number="07012341234",
    )
    assert url == "ws://localhost:3000/v1/accounts/AC123/agent/listen?number=07012341234"


def test_build_sdk_agent_url_with_retirement_role():
    url = build_control_ws_url(
        base_url="https://api.claw-ops.com",
        account_id="AC123",
        sdk_agent_id="front-desk_1",
        role="retiring",
    )
    assert url == "wss://api.claw-ops.com/v1/accounts/AC123/agent/listen?sdkAgentId=front-desk_1&role=retiring"
