"""Local web dashboard. All configuration changes pass through ReviewService."""
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .lab import DigitalTwin, Route
from .reviews import ReviewConflict, ReviewNotFound, ReviewService
from .state import StateStore, record_event
from .lab_console import LabConsole
from .backends import GNS3ReviewBackend, SimulatorReviewBackend

ASSETS = Path(__file__).parent / "assets"
SCENARIOS = ("success", "remove", "rejected", "conflict", "loop", "rollback", "verification")


def create_app(state_file=".ps008/state.json", gns3_inventory=None, *, console=None):
    app = FastAPI(title="PS-008 Network Management Agent", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
    app.mount("/static", StaticFiles(directory=ASSETS), name="static")
    templates = Jinja2Templates(directory=ASSETS)
    normal_store = StateStore(state_file)
    console = console or LabConsole(gns3_inventory, normal_store.path.parent / "gns3-evidence.json")
    sessions = {}

    @app.middleware("http")
    async def session_and_security(request: Request, call_next):
        now = time.time()
        for key in [k for k, value in sessions.items() if now - value["created"] > 8 * 3600]:
            sessions.pop(key)
        session_id = request.cookies.get("ps008_session")
        fresh = session_id not in sessions
        if fresh:
            session_id = secrets.token_hex(24)
            sessions[session_id] = {"csrf": secrets.token_urlsafe(32), "mode": "lab", "created": now}
        request.state.session = sessions[session_id]
        request.state.session_id = session_id
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = urlsplit(request.headers.get("origin", ""))
            if origin.scheme != request.url.scheme or origin.netloc != request.url.netloc:
                return JSONResponse({"error": "Same-origin request required"}, status_code=403)
            supplied = request.headers.get("x-csrf-token", "")
            if not secrets.compare_digest(supplied, request.state.session["csrf"]):
                return JSONResponse({"error": "Invalid session CSRF token"}, status_code=403)
            # Bound untrusted input before the parser or persistent review store.
            if len(await request.body()) > 16384:
                return JSONResponse({"error": "Request exceeds 16 KiB limit"}, status_code=413)
        response = await call_next(request)
        if fresh:
            response.set_cookie("ps008_session", session_id, httponly=True, samesite="strict", max_age=28800)
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    def store_for(request):
        if request.state.session["mode"] == "demo":
            return StateStore(normal_store.path.parent / "demos" / f"{request.state.session_id}.json")
        return normal_store

    def service(request):
        return ReviewService(store_for(request))

    def review_backend(request):
        return SimulatorReviewBackend(service(request))

    gns3_backend = GNS3ReviewBackend(console)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"error": str(error)}, status_code=409 if isinstance(error, ReviewConflict) else 422)

    @app.exception_handler(ReviewNotFound)
    async def not_found(request, error):
        return JSONResponse({"error": "Review not found in this workspace"}, status_code=404)

    @app.exception_handler(OSError)
    async def storage_error(request, error):
        return JSONResponse({"error": "Lab storage is unavailable; no change can be approved"}, status_code=503)

    @app.get("/")
    def dashboard(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={})

    @app.get("/api/session")
    def session(request: Request):
        return {"csrf_token": request.state.session["csrf"], "workspace": request.state.session["mode"]}

    @app.get("/gns3")
    def gns3_dashboard(request: Request):
        return templates.TemplateResponse(request=request, name="gns3.html", context={})

    @app.get("/api/gns3/status")
    def gns3_status():
        return console.status()

    @app.post("/api/gns3/capture")
    def gns3_capture(data: dict):
        if set(data) != {"mode"}:
            raise ValueError("Provide only the observation mode")
        return console.capture(data["mode"])

    @app.post("/api/gns3/preview")
    def gns3_preview(data: dict):
        return console.preview(data)

    @app.post("/api/gns3/reviews")
    def gns3_prepare_review(data: dict):
        return gns3_backend.prepare(data)

    @app.get("/api/gns3/reviews/{operation_id}")
    def gns3_review(operation_id: str):
        return gns3_backend.get(operation_id)

    @app.post("/api/gns3/reviews/{operation_id}/approve")
    def gns3_approve(operation_id: str):
        return gns3_backend.decide(operation_id, True)

    @app.post("/api/gns3/reviews/{operation_id}/reject")
    def gns3_reject(operation_id: str):
        return gns3_backend.decide(operation_id, False)

    @app.post("/api/gns3/reviews/{operation_id}/reconcile")
    def gns3_reconcile(operation_id: str):
        return gns3_backend.reconcile(operation_id)

    @app.get("/api/gns3/evidence")
    def gns3_evidence():
        return {"format": "ps008-evidence-v2", "notice": "Checksums detect changes; they do not prove authenticity. Modeled forwarding predictions are not packet tests. Device-observed probe results identify their source and time.", **console.status()}

    @app.get("/api/lab")
    def lab(request: Request):
        return {**service(request).lab(), "workspace": request.state.session["mode"]}

    @app.post("/api/workspace")
    def workspace(request: Request, data: dict):
        if set(data) != {"mode"} or data["mode"] not in ("lab", "demo"):
            raise ValueError("mode must be lab or demo")
        request.state.session["mode"] = data["mode"]
        return lab(request)

    @app.get("/api/trace")
    def trace(request: Request, source: str, destination: str):
        return service(request).trace(source, destination)

    @app.get("/api/audit")
    def audit(request: Request):
        return {"events": service(request).audit()}

    @app.post("/api/reviews")
    def prepare(request: Request, data: dict):
        return review_backend(request).prepare(data)

    @app.get("/api/reviews/{review_id}")
    def review(request: Request, review_id: str):
        return review_backend(request).get(review_id)

    @app.post("/api/reviews/{review_id}/approve")
    def approve(request: Request, review_id: str):
        return review_backend(request).decide(review_id, True)

    @app.post("/api/reviews/{review_id}/reject")
    def reject(request: Request, review_id: str):
        return review_backend(request).decide(review_id, False)

    @app.post("/api/demo/scenarios/{scenario}")
    def scenario_review(request: Request, scenario: str):
        if request.state.session["mode"] != "demo":
            raise ReviewConflict("Switch to the demo workspace first")
        if scenario not in SCENARIOS:
            raise ValueError("Unknown demo scenario")
        store = store_for(request)
        with store.locked():
            old = store._load_unlocked()
            twin = DigitalTwin.baseline()
            twin.revision = old.revision + 1
            twin.audit = old.audit
            twin.reviews = old.reviews
            for item in twin.reviews.values():
                if item["status"] == "pending":
                    item["status"] = "cancelled"
            if scenario == "remove":
                twin.routes["R1"].append(Route("10.20.0.0/24", "10.0.1.2", "R1"))
            elif scenario == "conflict":
                twin.routes["R1"].append(Route("10.20.0.0/24", "10.0.1.1", "R1"))
            elif scenario == "loop":
                twin.routes["R2"].append(Route("10.20.0.0/24", "10.0.1.1", "R2"))
            record_event(twin, "demo_reset", scenario=scenario)
            store.save(twin)
        data = {
            "intent_type": "remove_static_route" if scenario == "remove" else "add_static_route",
            "source": "R1", "destination": "10.20.0.0/24",
            "next_hop": None if scenario == "remove" else "10.0.1.2", "protocol": "static",
            "requested_change": f"Demonstrate {scenario}", "request_id": f"demo-{secrets.token_hex(6)}",
        }
        failure = "apply" if scenario == "rollback" else "verification" if scenario == "verification" else None
        return service(request).prepare_review(data, failure=failure)

    return app
