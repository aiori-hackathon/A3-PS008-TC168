import pytest
from fastapi.testclient import TestClient

from ps008.state import StateStore
from ps008.web import create_app
from test_reviews import request


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "state.json"), base_url="http://127.0.0.1") as client:
        token = client.get("/api/session").json()["csrf_token"]
        client.headers.update({"Origin": "http://127.0.0.1", "X-CSRF-Token": token})
        yield client


def test_api_approval_and_trace(client):
    review = client.post("/api/reviews", json=request()).json()
    assert client.get("/api/lab").json()["revision"] == 0
    result = client.post(f"/api/reviews/{review['review_id']}/approve", json={})
    assert result.status_code == 200 and result.json()["status"] == "deployed"
    assert client.get("/api/trace", params={"source": "R1", "destination": "10.20.0.10"}).json()["path"] == ["R1", "R2", "R3"]
    assert client.post(f"/api/reviews/{review['review_id']}/approve", json={}).status_code == 409


def test_api_validation_and_missing_review(client):
    assert client.post("/api/reviews", json={}).status_code == 422
    invalid = client.post("/api/reviews", json=request(next_hop="192.0.2.1")).json()
    assert invalid["status"] == "invalid"
    assert client.post(f"/api/reviews/{invalid['review_id']}/approve", json={}).status_code == 409
    assert client.get("/api/reviews/missing").status_code == 404
    assert client.get("/api/trace", params={"source": "R4", "destination": "bad"}).status_code == 422


def test_api_rejection(client):
    review = client.post("/api/reviews", json=request()).json()
    result = client.post(f"/api/reviews/{review['review_id']}/reject", json={})
    assert result.json()["status"] == "rejected"
    assert client.get("/api/lab").json()["routes"]["R1"] == []


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example", "X-CSRF-Token": "wrong"},
    {"Origin": "http://127.0.0.1", "X-CSRF-Token": "wrong"},
    {"Origin": "", "X-CSRF-Token": "wrong"},
])
def test_mutations_require_same_origin_and_session_csrf(client, headers):
    assert client.post("/api/reviews", json=request(), headers=headers).status_code == 403


def test_csrf_is_bound_to_cookie(tmp_path):
    app = create_app(tmp_path / "state.json")
    with TestClient(app, base_url="http://127.0.0.1") as first, TestClient(app, base_url="http://127.0.0.1") as second:
        a = first.get("/api/session").json()["csrf_token"]
        b = second.get("/api/session").json()["csrf_token"]
        assert a != b
        assert second.post("/api/reviews", json=request(), headers={"Origin":"http://127.0.0.1", "X-CSRF-Token":a}).status_code == 403


def test_demo_isolation_and_explicit_approval(client):
    assert client.post("/api/demo/scenarios/success", json={}).status_code == 409
    client.post("/api/workspace", json={"mode":"demo"})
    review = client.post("/api/demo/scenarios/success", json={}).json()
    assert review["status"] == "pending"
    assert client.get("/api/lab").json()["routes"]["R1"] == []
    client.post(f"/api/reviews/{review['review_id']}/approve", json={})
    client.post("/api/workspace", json={"mode":"lab"})
    assert client.get("/api/lab").json()["routes"]["R1"] == []
    assert client.get(f"/api/reviews/{review['review_id']}").status_code == 404


@pytest.mark.parametrize("scenario,status", [("conflict","invalid"),("loop","invalid"),
                                               ("rollback","rolled_back"),("verification","rolled_back"),
                                               ("remove","deployed")])
def test_demo_scenarios(client, scenario, status):
    client.post("/api/workspace", json={"mode":"demo"})
    review = client.post(f"/api/demo/scenarios/{scenario}", json={}).json()
    if status == "invalid":
        assert review["status"] == "invalid"
    else:
        before = client.get("/api/lab").json()
        result = client.post(f"/api/reviews/{review['review_id']}/approve", json={}).json()
        assert result["status"] == status
        if status == "rolled_back":
            assert client.get("/api/lab").json() == before


def test_raw_commands_and_failure_fields_are_not_public_input(client):
    assert client.post("/api/reviews", json=request(commands=["reload"])).status_code == 422
    assert client.post("/api/reviews", json=request(failure="apply")).status_code == 422


def test_packaged_assets_and_security_headers(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Evidence before execution" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert "HttpOnly" not in page.headers.get("set-cookie", "") or "SameSite=strict" in page.headers["set-cookie"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    assert client.post("/api/reviews", content="x" * 17000).status_code == 413
