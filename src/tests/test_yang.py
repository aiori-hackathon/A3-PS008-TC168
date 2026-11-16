import pytest
from ps008.schema import IntentRequest
from ps008.yang.netconf import intent_to_netconf_xml, netconf_xml_to_intent


def test_intent_to_netconf_xml_roundtrip():
    req = IntentRequest(
        intent_type="add_static_route",
        source="R1",
        destination="10.20.0.0/24",
        next_hop="10.0.1.2",
        protocol="static",
        requested_change="Connect R1 to engineering LAN",
        request_id="netconf-test-01",
    )
    xml_out = intent_to_netconf_xml(req)
    assert ":rpc" in xml_out or "<rpc" in xml_out
    assert "urn:ietf:params:xml:ns:netconf:base:1.0" in xml_out
    assert "urn:aiori:hackathon:ps008:intent" in xml_out
    assert "source-router>R1<" in xml_out
    assert "destination-prefix>10.20.0.0/24<" in xml_out

    parsed = netconf_xml_to_intent(xml_out)
    assert parsed.intent_type == req.intent_type
    assert parsed.source == req.source
    assert parsed.destination == req.destination
    assert parsed.next_hop == req.next_hop
    assert parsed.request_id == req.request_id


def test_invalid_netconf_xml_missing_container():
    bad_xml = "<rpc><bad-element/></rpc>"
    with pytest.raises(ValueError, match="Missing <network-intent>"):
        netconf_xml_to_intent(bad_xml)
