import ipaddress
from dataclasses import dataclass, field
from .schema import Candidate, ValidationResult

ROUTER_INTERFACES = {
    "R1": {"10.0.1.1"},
    "R2": {"10.0.1.2", "10.0.2.1"},
    "R3": {"10.0.2.2"},
}
NEXT_HOP_TO_ROUTER = {
    address: router
    for router, addresses in ROUTER_INTERFACES.items()
    for address in addresses
}
ALLOWED_NEXT_HOPS = {
    "R1": {"10.0.1.2"},
    "R2": {"10.0.1.1", "10.0.2.2"},
    "R3": {"10.0.2.1"},
}
CONNECTED_NETWORKS = {
    "R1": ("10.10.0.0/24", "10.0.1.0/24"),
    "R2": ("10.30.0.0/24", "10.0.1.0/24", "10.0.2.0/24"),
    "R3": ("10.20.0.0/24", "10.0.2.0/24"),
}
# Fixed lab bootstrap routes; operator-managed routes are stored separately.
BOOTSTRAP_ROUTES = {
    "R1": (),
    "R2": (("10.10.0.0/24", "10.0.1.1"), ("10.20.0.0/24", "10.0.2.2")),
    "R3": (("10.10.0.0/24", "10.0.2.1"), ("10.30.0.0/24", "10.0.2.1")),
}


@dataclass
class Route:
    network: str
    next_hop: str
    owner: str


@dataclass
class DigitalTwin:
    routers: tuple[str, ...] = ("R1", "R2", "R3")
    routes: dict[str, list[Route]] = field(default_factory=dict)
    revision: int = 0
    applied_request_ids: set[str] = field(default_factory=set)
    modeled: bool = False
    reviews: dict[str, dict] = field(default_factory=dict)
    audit: list[dict] = field(default_factory=list)

    @classmethod
    def baseline(cls):
        return cls(modeled=True)

    def clone(self):
        return DigitalTwin(routers=self.routers, routes=self.snapshot(), revision=self.revision,
                           applied_request_ids=self.applied_request_ids.copy(), modeled=self.modeled)

    def apply(self, candidate: Candidate):
        req = candidate.request
        self.routes[candidate.router] = [r for r in self.routes[candidate.router] if r.network != req.destination]
        if req.intent_type == "add_static_route":
            self.routes[candidate.router].append(Route(req.destination, req.next_hop, candidate.router))

    def topology(self):
        return {
            "routers": [{"id": r, "interfaces": sorted(ROUTER_INTERFACES[r]),
                         "connected": list(CONNECTED_NETWORKS[r]),
                         "bootstrap_routes": [{"network": n, "next_hop": h} for n, h in BOOTSTRAP_ROUTES[r]]}
                        for r in self.routers],
            "links": [{"source": "R1", "target": "R2", "network": "10.0.1.0/24"},
                      {"source": "R2", "target": "R3", "network": "10.0.2.0/24"}],
        }

    def trace(self, source: str, destination: str):
        if source not in self.routers:
            raise ValueError("Unknown source router")
        address = ipaddress.IPv4Address(destination)
        path, steps = [], []
        current = source
        while True:
            if current in path:
                return {"source": source, "destination": str(address), "path": path + [current],
                        "outcome": "loop", "steps": steps}
            path.append(current)
            matches = [n for n in CONNECTED_NETWORKS[current] if address in ipaddress.IPv4Network(n)]
            connected = max(matches, key=lambda n: ipaddress.IPv4Network(n).prefixlen) if matches else None
            route = self.route_for(current, address)
            if connected and (route is None or ipaddress.IPv4Network(connected).prefixlen >= ipaddress.IPv4Network(route.network).prefixlen):
                steps.append({"router": current, "route": connected, "kind": "connected"})
                return {"source": source, "destination": str(address), "path": path,
                        "outcome": "reached", "steps": steps}
            if route is None:
                return {"source": source, "destination": str(address), "path": path,
                        "outcome": "missing_route", "steps": steps}
            steps.append({"router": current, "route": route.network, "next_hop": route.next_hop,
                          "kind": "managed" if route in self.routes[current] else "bootstrap"})
            if route.next_hop not in ALLOWED_NEXT_HOPS[current]:
                return {"source": source, "destination": str(address), "path": path,
                        "outcome": "invalid_adjacency", "steps": steps}
            current = NEXT_HOP_TO_ROUTER[route.next_hop]

    def probes(self, network: str):
        """One address per forwarding-equivalence interval, plus subnet endpoints."""
        target = ipaddress.IPv4Network(network)
        start, end = int(target.network_address), int(target.broadcast_address)
        boundaries = {start, end}
        prefixes = [r.network for table in self.routes.values() for r in table]
        if self.modeled:
            prefixes += [n for values in CONNECTED_NETWORKS.values() for n in values]
            prefixes += [n for values in BOOTSTRAP_ROUTES.values() for n, _ in values]
        for prefix in prefixes:
            subnet = ipaddress.IPv4Network(prefix)
            if subnet.overlaps(target):
                boundaries.add(max(start, int(subnet.network_address)))
                after = int(subnet.broadcast_address) + 1
                if start <= after <= end:
                    boundaries.add(after)
        return [str(ipaddress.IPv4Address(x)) for x in sorted(boundaries)]

    def __post_init__(self):
        for router in self.routers:
            self.routes.setdefault(router, [])
        if set(self.routes) != set(self.routers):
            raise ValueError("State contains unknown or missing routers")

    def snapshot(self):
        return {r: [Route(x.network, x.next_hop, x.owner) for x in items] for r, items in self.routes.items()}

    def restore(self, snapshot):
        self.routes = {r: [Route(x.network, x.next_hop, x.owner) for x in items] for r, items in snapshot.items()}

    def state(self) -> dict[str, list[dict[str, str]]]:
        return {
            router: [
                {"network": route.network, "next_hop": route.next_hop, "owner": route.owner}
                for route in routes
            ]
            for router, routes in self.routes.items()
        }

    def route_for(self, router: str, address: ipaddress.IPv4Address, routes=None) -> Route | None:
        table = self.routes if routes is None else routes
        entries = list(table[router])
        if self.modeled:
            entries += [Route(n, h, "bootstrap") for n, h in BOOTSTRAP_ROUTES[router]
                        if not any(r.network == n for r in table[router])]
        matches = [route for route in entries if address in ipaddress.IPv4Network(route.network)]
        if not matches:
            return None
        return max(matches, key=lambda route: ipaddress.IPv4Network(route.network).prefixlen)

    def exact_route(self, router: str, network: str) -> Route | None:
        return next((route for route in self.routes.get(router, []) if route.network == network), None)

    def _would_create_loop(self, source: str, network: str, next_hop: str) -> bool:
        candidate_network = ipaddress.IPv4Network(network)
        proposed = self.snapshot()
        proposed[source] = [r for r in proposed[source] if r.network != network]
        proposed[source].append(Route(network, next_hop, source))
        probes = {candidate_network.network_address, candidate_network.broadcast_address}
        for table in proposed.values():
            for route in table:
                subnet = ipaddress.IPv4Network(route.network)
                if subnet.overlaps(candidate_network):
                    probes.add(max(subnet.network_address, candidate_network.network_address))
                    probes.add(min(subnet.broadcast_address, candidate_network.broadcast_address))
        for address in probes:
            visited: set[str] = set()
            current = source
            while current in proposed:
                if current in visited:
                    return True
                visited.add(current)
                route = self.route_for(current, address, proposed)
                if route is None:
                    break
                current = NEXT_HOP_TO_ROUTER.get(route.next_hop, "")
        return False

    def validate(self, candidate: Candidate) -> ValidationResult:
        req = candidate.request
        errors, warnings, affected = [], [], []
        from .generator import generate_candidate, is_allowlisted
        existing_route = self.exact_route(req.source, req.destination)
        expected = generate_candidate(req, candidate.base_revision,
                                      existing_route.next_hop if existing_route else None)
        # Legacy callers may generate a removal before resolving the current hop.
        if not (req.intent_type == "remove_static_route" and not existing_route):
            if candidate.router != expected.router or candidate.commands != expected.commands or candidate.change_summary != expected.change_summary:
                errors.append("Candidate differs from the generated configuration")
            if not all(is_allowlisted(command) for command in candidate.commands):
                errors.append("Candidate contains a non-allowlisted command")
        if req.request_id in self.applied_request_ids:
            errors.append("request_id has already been applied")
        if candidate.router not in self.routers:
            errors.append("Unknown router")
        network = str(ipaddress.IPv4Network(req.destination, strict=False))
        if req.next_hop and req.next_hop in ROUTER_INTERFACES.get(req.source, set()):
            errors.append("Next hop cannot be the source router")
        existing = [r for r in self.routes.get(candidate.router, []) if r.network == network]
        if len(existing) > 1:
            errors.append("Multiple routes already exist for destination")
        affected.append(f"{candidate.router}:{network}")
        if req.intent_type == "add_static_route":
            if existing and existing[0].next_hop != req.next_hop:
                errors.append("Conflicting route already exists for destination")
            if existing and existing[0].next_hop == req.next_hop:
                warnings.append("Route already exists; deployment is idempotent")
            next_router = NEXT_HOP_TO_ROUTER.get(req.next_hop or "")
            if next_router is None:
                errors.append("Next hop is outside the lab address space")
            elif req.next_hop not in ALLOWED_NEXT_HOPS.get(candidate.router, set()):
                errors.append("Next hop is not directly connected to the source router")
            elif not self.modeled and self._would_create_loop(candidate.router, network, req.next_hop):
                errors.append("Candidate route would create a routing loop")
        elif not existing:
            errors.append("No managed route exists to remove")
        elif existing[0].owner != candidate.router:
            errors.append("Route is not managed by the source router")
        impact = {}
        if self.modeled and candidate.router in self.routers:
            proposed = self.clone()
            proposed.apply(candidate)
            probes = sorted(set(self.probes(network) + proposed.probes(network)), key=ipaddress.IPv4Address)
            traces = [{"before": self.trace(req.source, address), "after": proposed.trace(req.source, address)}
                      for address in probes]
            if any(x["after"]["outcome"] == "loop" for x in traces):
                errors.append("Candidate route would create a routing loop")
            if req.intent_type == "add_static_route":
                if any(x["after"]["outcome"] not in ("reached", "loop") for x in traces):
                    errors.append("Candidate destination is not reachable in the modeled lab")
                if any(ipaddress.IPv4Network(network).subnet_of(ipaddress.IPv4Network(n)) for n in CONNECTED_NETWORKS[req.source]):
                    errors.append("Destination is already directly connected to the source router")
            elif any(x["before"]["outcome"] == "reached" and x["after"]["outcome"] != "reached" for x in traces):
                warnings.append("Removal causes loss of reachability; review the forwarding impact before approval")
            impact = {"before_routes": self.state(), "after_routes": proposed.state(), "traces": traces}
        groups = [
            ("Generated command integrity", ("Candidate", "allowlisted"), "Commands match the normalized operator intent"),
            ("Next-hop adjacency", ("Next hop", "Unknown router"), "Next hop belongs to the source router's adjacent link"),
            ("Route conflicts and request identity", ("Conflicting", "Multiple", "managed", "request_id", "directly connected to the source"), "No conflicting route or reused applied request ID"),
            ("Forwarding simulation", ("routing loop", "reachable"), "Every affected prefix interval was evaluated" if self.modeled else "Legacy topology-only validation"),
        ]
        checks = [{"name": name, "passed": not any(any(word in e for word in words) for e in errors),
                   "detail": detail} for name, words, detail in groups]
        return ValidationResult(not errors, errors, warnings, affected, checks, impact)
