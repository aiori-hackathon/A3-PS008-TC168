import pytest
from ps008.ingress import parse_natural_intent


def test_natural_language_add_intent():
    prompt = "Connect R1 to engineering LAN 10.20.0.0/24 via gateway 10.0.1.2"
    req = parse_natural_intent(prompt)
    assert req.intent_type == "add_static_route"
    assert req.source == "R1"
    assert req.destination == "10.20.0.0/24"
    assert req.next_hop == "10.0.1.2"
    assert req.protocol == "static"
    assert "Connect R1" in req.requested_change


def test_natural_language_remove_intent():
    prompt = "Remove static route 10.20.0.0/24 from R1"
    req = parse_natural_intent(prompt)
    assert req.intent_type == "remove_static_route"
    assert req.source == "R1"
    assert req.destination == "10.20.0.0/24"
    assert req.next_hop is None


def test_natural_language_injection_rejected():
    with pytest.raises(ValueError, match="blocked by guardrails"):
        parse_natural_intent("Connect R1 to 10.20.0.0/24 via 10.0.1.2 ; reload")

    with pytest.raises(ValueError, match="blocked by guardrails"):
        parse_natural_intent("Configure R1: show run")


def test_natural_language_missing_router_or_cidr():
    with pytest.raises(ValueError, match="Could not determine source router"):
        parse_natural_intent("Add route to 10.20.0.0/24 via 10.0.1.2")

    with pytest.raises(ValueError, match="CIDR format"):
        parse_natural_intent("Connect R1 to engineering via 10.0.1.2")
