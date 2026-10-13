"""Synthetic evidence fixtures for an explicitly labeled offline rehearsal."""
from .lab import ROUTER_INTERFACES, CONNECTED_NETWORKS, BOOTSTRAP_ROUTES


def observations():
    output = []
    for name in ("R1", "R2", "R3"):
        interfaces = ["Interface IP-Address OK? Method Status Protocol"]
        routes = ["Codes: C - connected, S - static, L - local", "Gateway of last resort is not set"]
        for index, network in enumerate(CONNECTED_NETWORKS[name]):
            import ipaddress
            subnet = ipaddress.IPv4Network(network)
            address = next((a for a in ROUTER_INTERFACES[name] if ipaddress.IPv4Address(a) in subnet), str(subnet.network_address + 1))
            interface = f"GigabitEthernet0/{index}"
            interfaces.append(f"{interface} {address} YES manual up up")
            routes.append(f"C {network} is directly connected, {interface}")
        for network, hop in BOOTSTRAP_ROUTES[name]:
            routes.append(f"S {network} [1/0] via {hop}")
        output.append({"device": name, "node_id": f"rehearsal-{name}",
                       "interfaces_raw": "\n".join(interfaces), "routes_raw": "\n".join(routes),
                       "evidence": "synthetic-rehearsal"})
    return output
