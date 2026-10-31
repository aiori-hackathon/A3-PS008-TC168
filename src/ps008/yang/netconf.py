"""RFC 6241 (NETCONF) and RFC 7950 (YANG) translation utilities for PS-008."""

import xml.etree.ElementTree as ET
from xml.dom import minidom
from ..schema import IntentRequest

NETCONF_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
PS008_NS = "urn:aiori:hackathon:ps008:intent"

# Register standard namespace prefixes for clean serialization
ET.register_namespace("nc", NETCONF_NS)
ET.register_namespace("ps008", PS008_NS)


def intent_to_netconf_xml(request: IntentRequest, message_id: str = "1") -> str:
    """Serialize an IntentRequest into a canonical RFC 6241 NETCONF <rpc> XML message."""
    rpc = ET.Element(f"{{{NETCONF_NS}}}rpc", attrib={"message-id": message_id})
    submit = ET.SubElement(rpc, f"{{{PS008_NS}}}submit-intent")
    intent = ET.SubElement(submit, f"{{{PS008_NS}}}network-intent")

    fields = [
        ("request-id", request.request_id),
        ("intent-type", request.intent_type.replace("_", "-")),
        ("source-router", request.source),
        ("destination-prefix", request.destination),
        ("protocol", request.protocol),
        ("requested-change", request.requested_change),
    ]
    if request.next_hop:
        fields.append(("next-hop", request.next_hop))

    for tag, val in fields:
        child = ET.SubElement(intent, f"{{{PS008_NS}}}{tag}")
        child.text = str(val)

    rough = ET.tostring(rpc, encoding="utf-8")
    parsed = minidom.parseString(rough)
    return parsed.toprettyxml(indent="  ")


def netconf_xml_to_intent(xml_content: str) -> IntentRequest:
    """Parse an RFC 6241 NETCONF <rpc> message containing a ps008 intent into an IntentRequest."""
    root = ET.fromstring(xml_content)
    # Support both namespaced and non-namespaced inputs
    intent_elem = root.find(f".//{{{PS008_NS}}}network-intent")
    if intent_elem is None:
        intent_elem = root.find(".//network-intent")
    if intent_elem is None:
        # Check without namespace by stripping tags
        for el in root.iter():
            if el.tag.split("}")[-1] == "network-intent":
                intent_elem = el
                break
    if intent_elem is None:
        raise ValueError("Missing <network-intent> element in NETCONF payload")

    def get_val(tag: str, default=None):
        elem = intent_elem.find(f"{{{PS008_NS}}}{tag}")
        if elem is None:
            elem = intent_elem.find(tag)
        if elem is None:
            for el in intent_elem.iter():
                if el.tag.split("}")[-1] == tag:
                    elem = el
                    break
        return elem.text.strip() if elem is not None and elem.text else default

    intent_type = get_val("intent-type", "").replace("-", "_")
    source = get_val("source-router", "")
    dest = get_val("destination-prefix", "")
    next_hop = get_val("next-hop")
    proto = get_val("protocol", "static")
    change = get_val("requested-change", "")
    req_id = get_val("request-id", "")

    return IntentRequest(
        intent_type=intent_type,  # type: ignore
        source=source,
        destination=dest,
        next_hop=next_hop,
        protocol=proto,
        requested_change=change,
        request_id=req_id,
    )
