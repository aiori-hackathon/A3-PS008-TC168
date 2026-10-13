import copy
import pytest
from ps008.deployment import DeploymentManager
from ps008.cli import run_scenarios
from ps008.generator import generate_candidate, parse_request
from ps008.lab import DigitalTwin, Route
from ps008.workflow import run_workflow


def req(**overrides):
    value = {"intent_type":"add_static_route","source":"R1","destination":"10.20.0.0/24","next_hop":"10.0.1.2","protocol":"static","requested_change":"add","request_id":"t-1"}
    value.update(overrides)
    return value


def test_add_request_parses_and_generates_command():
    c = generate_candidate(parse_request(req()))
    assert c.commands == ("ip route 10.20.0.0 255.255.255.0 10.0.1.2",)

def test_remove_request_generates_no_command():
    c = generate_candidate(parse_request(req(intent_type="remove_static_route", next_hop=None)), remove_next_hop="10.0.1.2")
    assert c.commands == ("no ip route 10.20.0.0 255.255.255.0 10.0.1.2",)

@pytest.mark.parametrize("field", ["intent_type", "source", "destination", "protocol", "requested_change", "request_id"])
def test_missing_required_field_rejected(field):
    data = req(); data.pop(field)
    with pytest.raises(ValueError): parse_request(data)

def test_bad_next_hop_rejected():
    with pytest.raises(ValueError): parse_request(req(next_hop="not-an-ip"))

def test_bad_destination_rejected():
    with pytest.raises(ValueError): parse_request(req(destination="not-a-network"))

def test_unsupported_intent_rejected():
    with pytest.raises(ValueError): parse_request(req(intent_type="run_commands"))

def test_unknown_router_rejected():
    with pytest.raises(ValueError): parse_request(req(source="R4"))

def test_unsupported_protocol_rejected():
    with pytest.raises(ValueError): parse_request(req(protocol="bgp"))

def test_self_hop_rejected():
    result = DigitalTwin().validate(generate_candidate(parse_request(req(next_hop="10.0.1.1"))))
    assert not result.valid and "source router" in result.errors[0]

def test_outside_lab_hop_rejected():
    result = DigitalTwin().validate(generate_candidate(parse_request(req(next_hop="192.168.1.1"))))
    assert not result.valid

def test_non_neighbor_hop_rejected():
    result = DigitalTwin().validate(generate_candidate(parse_request(req(next_hop="10.0.2.2"))))
    assert not result.valid and any("directly connected" in e for e in result.errors)

def test_forwarding_loop_rejected():
    twin = DigitalTwin()
    twin.routes["R2"].append(Route("10.20.0.0/24", "10.0.1.1", "R2"))
    result = twin.validate(generate_candidate(parse_request(req())))
    assert not result.valid and any("routing loop" in e for e in result.errors)

def test_conflicting_route_rejected():
    twin = DigitalTwin(); twin.routes["R1"].append(Route("10.20.0.0/24", "10.0.1.3", "R1"))
    result = twin.validate(generate_candidate(parse_request(req())))
    assert not result.valid and any("Conflicting" in e for e in result.errors)

def test_missing_route_removal_rejected():
    result = DigitalTwin().validate(generate_candidate(parse_request(req(intent_type="remove_static_route", next_hop=None))))
    assert not result.valid

def test_human_rejection_blocks_deployment():
    result = run_workflow(req(), lambda _: False)
    assert not result["deployment"].success and "rejected" in result["deployment"].message

def test_approved_deployment_changes_twin():
    twin = DigitalTwin(); result = run_workflow(req(), lambda _: True, twin)
    assert result["deployment"].success and twin.routes["R1"][0].network == "10.20.0.0/24"

def test_approved_removal_changes_twin():
    twin = DigitalTwin()
    twin.routes["R1"].append(Route("10.20.0.0/24", "10.0.1.2", "R1"))
    result = run_workflow(req(intent_type="remove_static_route", next_hop=None), lambda _: True, twin)
    assert result["deployment"].success and twin.routes["R1"] == []

def test_failed_deployment_rolls_back():
    twin = DigitalTwin(); manager = DeploymentManager(twin); manager.fail_next = True
    candidate = generate_candidate(parse_request(req())); before = copy.deepcopy(twin.routes)
    result = manager.deploy(candidate, True)
    assert not result.success and result.rolled_back and twin.routes == before

def test_non_allowlisted_command_blocked():
    twin = DigitalTwin(); manager = DeploymentManager(twin); candidate = generate_candidate(parse_request(req()))
    candidate = type(candidate)(candidate.request, candidate.router, ("reload",), candidate.change_summary)
    result = manager.deploy(candidate, True)
    assert not result.success and "allowlisted" in result.message

def test_direct_deployment_still_requires_valid_candidate():
    twin = DigitalTwin()
    candidate = generate_candidate(parse_request(req(next_hop="192.168.1.1")))
    result = DeploymentManager(twin).deploy(candidate, True)
    assert not result.success and "failed validation" in result.message

def test_idempotent_route_is_warning():
    twin = DigitalTwin(); candidate = generate_candidate(parse_request(req()))
    manager = DeploymentManager(twin); assert manager.deploy(candidate, True).success
    retry = generate_candidate(parse_request(req(request_id="t-2")))
    result = twin.validate(retry)
    assert result.valid and result.warnings

def test_full_demo_covers_required_outcomes():
    results = {result["scenario"]: result for result in run_scenarios("full")}
    assert results["success"]["deployment"]["success"]
    assert results["remove"]["deployment"]["success"]
    assert results["remove"]["state"]["R1"] == []
    assert not results["rejected"]["deployment"]["success"]
    assert not results["invalid"]["validation"]["valid"]
    assert results["rollback"]["deployment"]["rolled_back"]
    assert results["rollback"]["state"] == {"R1": [], "R2": [], "R3": []}
