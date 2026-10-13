"""Restricted IPv4 forwarding analysis from explicit IOS observations.

No bootstrap routes are invented. Unsupported RIB formats fail closed.
Results predict forwarding to a connected network, not end-host availability.
"""
import copy
import hashlib
import ipaddress
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalize(observation):
    interfaces = []
    header = False
    for raw in observation["interfaces_raw"].splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Interface") and "IP-Address" in line:
            header = True
            continue
        match = re.fullmatch(r"(\S+)\s+(\S+)\s+(?:YES|NO)\s+\S+\s+(up|down|administratively down)\s+(up|down)", line)
        if not header or not match:
            raise ValueError("Unsupported interface output; complete IOS interface-brief evidence is required")
        name, address, status, protocol = match.groups()
        if address != "unassigned":
            address = str(ipaddress.IPv4Address(address))
        interfaces.append({"name": name, "address": address, "up": status == protocol == "up"})
    if not header or not interfaces or len({i["name"] for i in interfaces}) != len(interfaces):
        raise ValueError("Missing or duplicate interface evidence")
    routes = []
    in_legend = False
    for raw in observation["routes_raw"].splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Codes:"):
            in_legend = True
            continue
        if line.startswith("Gateway of last resort"):
            in_legend = False
            continue
        if in_legend and " - " in line:
            continue
        if re.fullmatch(r"[0-9./]+ is (?:variably )?subnetted, .*", line):
            in_legend = False
            continue
        connected = re.fullmatch(r"([CL])\s+([0-9.]+/\d+) is directly connected, (\S+)", line)
        static = re.fullmatch(r"S\*?\s+([0-9.]+/\d+) \[1/0\] via ([0-9.]+)", line)
        if connected:
            kind, prefix, interface = connected.groups()
            routes.append({"network": str(ipaddress.IPv4Network(prefix, strict=True)),
                           "kind": "connected" if kind == "C" else "local", "interface": interface,
                           "next_hop": None})
        elif static:
            prefix, hop = static.groups()
            routes.append({"network": str(ipaddress.IPv4Network(prefix, strict=True)),
                           "kind": "static", "interface": None, "next_hop": str(ipaddress.IPv4Address(hop))})
        else:
            raise ValueError("Unsupported routing output: only explicit CIDR connected/local and simple static routes are supported")
        in_legend = False
    if not routes or len({r["network"] for r in routes}) != len(routes):
        raise ValueError("Missing routes or multiple next hops for the same prefix")
    active = {i["name"] for i in interfaces if i["up"]}
    if any(r["kind"] in ("connected", "local") and r["interface"] not in active for r in routes):
        raise ValueError("Route/interface evidence is inconsistent")
    result = {"device": observation["device"], "node_id": observation["node_id"],
              "interfaces": sorted(interfaces, key=lambda x: x["name"]),
              "routes": sorted(routes, key=lambda x: x["network"])}
    result["fingerprint"] = digest(result)
    return result


def trace(network, source, destination):
    address = ipaddress.IPv4Address(destination)
    nodes = {n["device"]: n for n in network}
    if source not in nodes:
        raise ValueError("Source router has not been observed")
    addresses = {}
    for node in network:
        for interface in node["interfaces"]:
            if interface["up"] and interface["address"] != "unassigned":
                addresses.setdefault(interface["address"], []).append(node["device"])
    path, steps = [], []
    current = source
    while True:
        if current in path:
            return {"path": path + [current], "steps": steps, "outcome": "loop"}
        path.append(current)
        rows = nodes[current]["routes"]
        matches = [r for r in rows if address in ipaddress.IPv4Network(r["network"])]
        if not matches:
            return {"path": path, "steps": steps, "outcome": "missing_route"}
        route = max(matches, key=lambda r: ipaddress.IPv4Network(r["network"]).prefixlen)
        steps.append({"router": current, **route})
        if route["kind"] in ("connected", "local"):
            return {"path": path, "steps": steps, "outcome": "connected_network"}
        peers = addresses.get(route["next_hop"], [])
        direct = any(r["kind"] == "connected" and ipaddress.IPv4Address(route["next_hop"]) in ipaddress.IPv4Network(r["network"]) for r in rows)
        if len(peers) != 1 or not direct:
            return {"path": path, "steps": steps, "outcome": "unresolved_next_hop"}
        current = peers[0]


def preview(network, request):
    """Counterfactual proposal only: never mutate captured evidence or devices."""
    source = next((n for n in network if n["device"] == request.source), None)
    if source is None:
        raise ValueError("Observe the source router before preview")
    prefix = ipaddress.IPv4Network(request.destination)
    existing = [r for r in source["routes"] if r["network"] == request.destination]
    if any(r["kind"] != "static" for r in existing):
        raise ValueError("Connected/local routes cannot be changed")
    if request.intent_type == "add_static_route":
        if any(r["kind"] == "connected" and prefix.subnet_of(ipaddress.IPv4Network(r["network"])) for r in source["routes"]):
            raise ValueError("Destination is already directly connected")
        if existing and existing[0]["next_hop"] != request.next_hop:
            raise ValueError("A conflicting static route is installed")
        owners = [(n["device"], i) for n in network for i in n["interfaces"] if i["up"] and i["address"] == request.next_hop]
        if len(owners) != 1 or owners[0][0] == request.source:
            raise ValueError("Next hop must identify one observed adjacent peer")
        if not any(r["kind"] == "connected" and ipaddress.IPv4Address(request.next_hop) in ipaddress.IPv4Network(r["network"]) for r in source["routes"]):
            raise ValueError("Next hop is not directly connected")
    elif not existing:
        raise ValueError("No installed static route to remove")
    after = copy.deepcopy(network)
    changed = next(n for n in after if n["device"] == request.source)
    changed["routes"] = [r for r in changed["routes"] if r["network"] != request.destination]
    if request.intent_type == "add_static_route":
        changed["routes"].append({"network": request.destination, "next_hop": request.next_hop, "kind": "static", "interface": None})
    # Test every forwarding interval inside the target prefix, from every observed source.
    start, end = int(prefix.network_address), int(prefix.broadcast_address)
    probes = {start, end}
    for node in network:
        for route in node["routes"]:
            subnet = ipaddress.IPv4Network(route["network"])
            if subnet.overlaps(prefix):
                probes.add(max(start, int(subnet.network_address)))
                boundary = int(subnet.broadcast_address) + 1
                if start <= boundary <= end:
                    probes.add(boundary)
    checks = []
    for node in network:
        for point in sorted(probes):
            ip = str(ipaddress.IPv4Address(point))
            before_path, after_path = trace(network, node["device"], ip), trace(after, node["device"], ip)
            checks.append({"source": node["device"], "destination": ip, "before": before_path, "after": after_path})
    regressions = [c for c in checks if c["before"]["outcome"] == "connected_network" and c["after"]["outcome"] != "connected_network"]
    loops = [c for c in checks if c["after"]["outcome"] == "loop"]
    source_checks = [c for c in checks if c["source"] == request.source]
    valid = not loops and (request.intent_type == "remove_static_route" or all(c["after"]["outcome"] == "connected_network" for c in source_checks))
    from .generator import generate_candidate
    candidate = generate_candidate(request, remove_next_hop=existing[0]["next_hop"] if existing else None)
    return {"valid": valid, "commands": list(candidate.commands), "summary": candidate.change_summary,
            "checks": checks, "regression_count": len(regressions), "loop_count": len(loops),
            "fingerprint": digest(network), "deployment_enabled": False,
            "scope": "Modeled forwarding from captured RIB evidence; no packet probes or configuration writes"}
