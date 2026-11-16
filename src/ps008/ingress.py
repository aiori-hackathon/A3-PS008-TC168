"""Guardrailed Natural Language Intent Ingress for PS-008.

Translates human-operator natural language requests into structured,
type-safe IntentRequests. Follows strict security boundaries:
- Never executes arbitrary CLI strings.
- Only maps into authorized 'add_static_route' or 'remove_static_route'.
- Enforces strict IPv4 CIDR and router whitelist.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
from .schema import IntentRequest

ROUTERS = {"R1", "R2", "R3"}
CIDR_PATTERN = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\b")
IP_PATTERN = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
ROUTER_PATTERN = re.compile(r"\b([rR][1-3])\b")


def parse_natural_intent(text: str, request_id: str | None = None) -> IntentRequest:
    """Parse a natural language operator request into a strictly validated IntentRequest.
    
    Examples:
      - "Connect R1 to engineering LAN 10.20.0.0/24 via 10.0.1.2"
      - "Add static route on R1 for 10.20.0.0/24 with next-hop 10.0.1.2"
      - "Remove static route 10.20.0.0/24 from R1"
      - "Delete route to 10.20.0.0/24 on R1"
    """
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("Natural language prompt cannot be empty")

    lower = clean_text.lower()

    # Reject dangerous CLI or shell patterns
    forbidden_tokens = ["reload", "sh ", "show ", "write ", "erase", "format", "conf t", "configure", ";", "|", "&"]
    for token in forbidden_tokens:
        if token in lower:
            raise ValueError(f"Direct CLI or shell command token '{token.strip()}' is blocked by guardrails")

    # Determine intent type
    is_remove = any(verb in lower for verb in ["remove", "delete", "withdraw", "drop", "teardown"])
    intent_type = "remove_static_route" if is_remove else "add_static_route"

    # Identify router
    router_match = ROUTER_PATTERN.search(clean_text)
    if not router_match:
        raise ValueError("Could not determine source router (must mention R1, R2, or R3)")
    source_router = router_match.group(1).upper()

    # Identify destination CIDR
    cidr_match = CIDR_PATTERN.search(clean_text)
    if not cidr_match:
        raise ValueError("Could not identify destination network in CIDR format (e.g. 10.20.0.0/24)")
    destination = cidr_match.group(1)
    try:
        network = ipaddress.IPv4Network(destination, strict=False)
        canonical_dest = str(network)
    except Exception as exc:
        raise ValueError(f"Invalid IPv4 destination network: {exc}")

    # Identify next-hop (required for add)
    next_hop = None
    if intent_type == "add_static_route":
        # Look for IPs that are not the destination network address
        all_ips = IP_PATTERN.findall(clean_text)
        dest_base_ip = destination.split("/")[0]
        candidate_hops = [ip for ip in all_ips if ip != dest_base_ip and "/" not in ip]

        # Prioritize IPs following 'via', 'through', 'next-hop', 'gateway', 'to'
        explicit_hop = re.search(r"(?:via|through|next-hop|hop|gateway)\s+(\d{1,3}(?:\.\d{1,3}){3})", clean_text, re.IGNORECASE)
        if explicit_hop:
            next_hop = explicit_hop.group(1)
        elif candidate_hops:
            next_hop = candidate_hops[0]
        else:
            raise ValueError("Next-hop IPv4 gateway must be specified for route additions (e.g. 'via 10.0.1.2')")

        try:
            ipaddress.IPv4Address(next_hop)
        except Exception as exc:
            raise ValueError(f"Invalid next-hop gateway address: {exc}")

    req_id = request_id.strip() if request_id and request_id.strip() else f"nl-{secrets.token_hex(4)}"

    return IntentRequest(
        intent_type=intent_type,
        source=source_router,
        destination=canonical_dest,
        next_hop=next_hop,
        protocol="static",
        requested_change=clean_text,
        request_id=req_id,
    )
