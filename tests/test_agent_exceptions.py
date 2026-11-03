# tests/test_agent_exceptions.py
from clawops._exceptions import AgentError, AgentConnectionError


def test_agent_errors_are_public_on_clawops_agent():
    import clawops.agent

    assert clawops.agent.AgentError is AgentError
    assert clawops.agent.AgentConnectionError is AgentConnectionError


def test_agent_error_is_clawops_error():
    from clawops._exceptions import ClawOpsError
    err = AgentError("test")
    assert isinstance(err, ClawOpsError)


def test_agent_connection_error_is_agent_error():
    err = AgentConnectionError("ws failed")
    assert isinstance(err, AgentError)
    assert str(err) == "ws failed"
