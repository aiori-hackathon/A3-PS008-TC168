import ipaddress
from .schema import Candidate, IntentRequest

ROUTERS = {"R1", "R2", "R3"}
ALLOWED_INTENTS = {"add_static_route", "remove_static_route"}


def parse_request(data: dict) -> IntentRequest:
    if not isinstance(data, dict):
        raise ValueError("Request must be a JSON object")
    required = {"intent_type", "source", "destination", "protocol", "requested_change", "request_id"}
    missing = sorted(required - data.keys())
    if missing:
        raise ValueError(f"Missing fields: {', '.join(missing)}")
    unknown = sorted(data.keys() - set(IntentRequest.__dataclass_fields__))
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(unknown)}")
    if not isinstance(data["intent_type"], str) or data["intent_type"] not in ALLOWED_INTENTS:
        raise ValueError("Unsupported intent_type")
    if not isinstance(data["source"], str) or data["source"] not in ROUTERS:
        raise ValueError("source must be one of R1, R2, R3")
    if data["protocol"] != "static":
        raise ValueError("Only static protocol is supported")
    for name in ("requested_change", "request_id"):
        if not isinstance(data[name], str) or not data[name].strip():
            raise ValueError(f"{name} must be a nonempty string")
        limit = 2000 if name == "requested_change" else 120
        if len(data[name].strip()) > limit:
            raise ValueError(f"{name} exceeds {limit} characters")
    if not isinstance(data["destination"], str) or "/" not in data["destination"]:
        raise ValueError("destination must be an IPv4 network in CIDR format")
    try:
        network = ipaddress.IPv4Network(data["destination"], strict=False)
    except (ValueError, ipaddress.AddressValueError) as exc:
        raise ValueError(f"Invalid destination network: {exc}") from exc
    hop = data.get("next_hop")
    if data["intent_type"] == "add_static_route" and not data.get("next_hop"):
        raise ValueError("next_hop is required for add_static_route")
    if data["intent_type"] == "remove_static_route" and hop is not None:
        raise ValueError("next_hop must be null for remove_static_route")
    if hop is not None:
        if not isinstance(hop, str):
            raise ValueError("next_hop must be an IPv4 address string")
        try:
            hop = str(ipaddress.IPv4Address(hop))
        except (ValueError, ipaddress.AddressValueError) as exc:
            raise ValueError(f"Invalid next_hop address: {exc}") from exc
    return IntentRequest(
        intent_type=data["intent_type"], source=data["source"],
        destination=str(network), next_hop=hop, protocol="static",
        requested_change=data["requested_change"].strip(),
        request_id=data["request_id"].strip(),
    )


def generate_candidate(
    request: IntentRequest,
    base_revision: int | None = None,
    remove_next_hop: str | None = None,
) -> Candidate:
    network = ipaddress.IPv4Network(request.destination, strict=False)
    prefix = f"{network.network_address} {network.netmask}"
    if request.intent_type == "add_static_route":
        command = f"ip route {prefix} {request.next_hop}"
        summary = f"Add static route {network} via {request.next_hop} on {request.source}"
    else:
        command = f"no ip route {prefix}" + (f" {remove_next_hop}" if remove_next_hop else "")
        summary = f"Remove managed static route {network} on {request.source}"
    return Candidate(request, request.source, (command,), summary, base_revision)


def is_allowlisted(command: str) -> bool:
    if not isinstance(command, str):
        return False
    parts = command.split(" ")
    if parts[:2] == ["ip", "route"] and len(parts) == 5:
        values = parts[2:]
    elif parts[:3] == ["no", "ip", "route"] and len(parts) == 6:
        values = parts[3:]
    else:
        return False
    if "" in parts:
        return False
    try:
        network = ipaddress.IPv4Network((values[0], values[1]), strict=True)
        if len(values) == 3:
            ipaddress.IPv4Address(values[2])
    except (ValueError, ipaddress.AddressValueError):
        return False
    return str(network.network_address) == values[0] and str(network.netmask) == values[1]
