"""HTTP API tests."""

from fastapi.testclient import TestClient

from deleezy_optimizer.main import app

client = TestClient(app)


def _route_payload() -> dict:
    return {
        "depot": {"lat": 40.7128, "lng": -74.006},
        "vehicles": [
            {
                "id": "van-1",
                "capacity": 10,
                "shift_start": "08:00",
                "shift_end": "18:00",
            }
        ],
        "stops": [
            {"id": "a", "lat": 40.72, "lng": -74.0, "demand": 2},
            {"id": "b", "lat": 40.73, "lng": -74.01, "demand": 1},
        ],
    }


def test_health_and_index() -> None:
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_optimize_endpoint() -> None:
    response = client.post("/api/v1/routes/optimize", json=_route_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["assigned_stops"] == 2
    assert body["routes"][0]["vehicle_id"] == "van-1"


def test_optimize_rejects_invalid_payload() -> None:
    payload = _route_payload()
    payload["depot"]["lat"] = 200
    response = client.post("/api/v1/routes/optimize", json=payload)
    assert response.status_code == 422


def test_optimize_rejects_unknown_fields() -> None:
    payload = _route_payload()
    payload["unexpected"] = True
    response = client.post("/api/v1/routes/optimize", json=payload)
    assert response.status_code == 422


def test_break_and_locate_endpoints() -> None:
    stops = [
        {"id": "s0", "lat": 40.700, "lng": -74.000, "demand": 1},
        {"id": "s1", "lat": 40.700, "lng": -73.998, "demand": 1},
        {"id": "s2", "lat": 40.702, "lng": -74.000, "demand": 1},
        {"id": "s3", "lat": 40.702, "lng": -73.998, "demand": 1},
        {"id": "s4", "lat": 40.700, "lng": -73.996, "demand": 1},
        {"id": "s5", "lat": 40.702, "lng": -73.996, "demand": 1},
    ]
    break_response = client.post(
        "/api/v1/zones/break",
        json={"stops": stops, "zone_count": 2, "max_stops_per_zone": 3},
    )
    assert break_response.status_code == 200
    body = break_response.json()
    assert body["summary"]["assigned_stops"] == 6
    assert len(body["zones"]) == 2

    locate_response = client.post(
        "/api/v1/zones/locate",
        json={"point": body["zones"][0]["centroid"], "zones": body["zones"]},
    )
    assert locate_response.status_code == 200
    located = locate_response.json()
    assert located["inside"] is True
    assert located["zone_id"] is not None


def test_break_rejects_impossible_capacity() -> None:
    stops = [{"id": f"s{i}", "lat": 40.70 + i * 0.001, "lng": -74.0} for i in range(10)]
    response = client.post(
        "/api/v1/zones/break",
        json={"stops": stops, "zone_count": 2, "max_stops_per_zone": 3},
    )
    assert response.status_code == 422
    assert "at least 4" in response.json()["detail"]
