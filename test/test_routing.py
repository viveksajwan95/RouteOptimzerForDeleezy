"""Route optimizer tests."""

from __future__ import annotations

import itertools

from deleezy_optimizer.schemas import RouteOptimizeRequest
from deleezy_optimizer.services.routing import optimize_routes

DEPOT = {"lat": 40.7128, "lng": -74.006}


def _request(
    stops: list[dict],
    vehicles: list[dict] | None = None,
    options: dict | None = None,
) -> RouteOptimizeRequest:
    payload = {
        "depot": DEPOT,
        "vehicles": vehicles
        or [
            {
                "id": "van-1",
                "capacity": 20,
                "max_stops": 20,
                "shift_start": "00:00",
                "shift_end": "23:59",
            }
        ],
        "stops": stops,
        "options": options or {"default_service_time_s": 0},
    }
    return RouteOptimizeRequest.model_validate(payload)


def _stop(stop_id: str, lat: float, lng: float, **kwargs) -> dict:
    return {"id": stop_id, "lat": lat, "lng": lng, **kwargs}


def test_single_vehicle_covers_all_stops() -> None:
    result = optimize_routes(
        _request(
            [
                _stop("a", 40.72, -74.0),
                _stop("b", 40.73, -74.01),
                _stop("c", 40.70, -73.99),
                _stop("d", 40.715, -74.02),
            ]
        )
    )
    assert result.summary.assigned_stops == 4
    assert result.summary.unassigned_stops == 0
    assert len(result.routes) == 1
    assert set(result.routes[0].stop_ids) == {"a", "b", "c", "d"}
    assert result.routes[0].peak_load == 4


def test_capacity_forces_multiple_routes() -> None:
    stops = [_stop(f"s{i}", 40.71 + i * 0.005, -74.0 + i * 0.005, demand=3) for i in range(4)]
    result = optimize_routes(
        _request(
            stops,
            vehicles=[
                {"id": "v1", "capacity": 6, "shift_start": "00:00", "shift_end": "23:59"},
                {"id": "v2", "capacity": 6, "shift_start": "00:00", "shift_end": "23:59"},
            ],
        )
    )
    assert len(result.routes) == 2
    assert result.summary.assigned_stops == 4
    assert all(route.peak_load <= 6 for route in result.routes)
    assert sum(route.stop_count for route in result.routes) == 4


def test_time_windows_are_respected() -> None:
    result = optimize_routes(
        _request(
            [
                _stop(
                    "morning",
                    40.72,
                    -74.0,
                    time_window={"start": "10:00", "end": "10:30"},
                ),
                _stop(
                    "noon",
                    40.73,
                    -74.01,
                    time_window={"start": "11:00", "end": "11:30"},
                ),
            ],
            vehicles=[
                {
                    "id": "v1",
                    "capacity": 10,
                    "shift_start": "08:00",
                    "shift_end": "18:00",
                }
            ],
        )
    )
    assert result.summary.assigned_stops == 2
    windows = {"morning": (10 * 3600, 10 * 3600 + 1800), "noon": (11 * 3600, 11 * 3600 + 1800)}
    for visit in result.routes[0].visits:
        start, end = windows[visit.stop_id]
        assert start <= visit.service_start_s <= end


def test_impossible_time_window_is_unassigned() -> None:
    result = optimize_routes(
        _request(
            [
                _stop(
                    "too-early",
                    40.72,
                    -74.0,
                    time_window={"start": "07:00", "end": "07:30"},
                )
            ],
            vehicles=[
                {
                    "id": "v1",
                    "capacity": 10,
                    "shift_start": "08:00",
                    "shift_end": "18:00",
                }
            ],
        )
    )
    assert result.summary.assigned_stops == 0
    assert result.unassigned[0].stop_id == "too-early"
    assert "time window" in result.unassigned[0].reason


def test_paired_pickup_and_dropoff_stay_together_in_order() -> None:
    result = optimize_routes(
        _request(
            [
                _stop("pickup", 40.72, -74.0, kind="pickup", pair_id="p1", demand=2),
                _stop("dropoff", 40.73, -74.01, kind="dropoff", pair_id="p1", demand=2),
                _stop("delivery", 40.70, -73.99, demand=1),
            ]
        )
    )
    route = result.routes[0]
    assert set(route.stop_ids) == {"pickup", "dropoff", "delivery"}
    assert route.stop_ids.index("pickup") < route.stop_ids.index("dropoff")
    assert route.peak_load <= 3


def test_unassigned_pair_reports_both_stops() -> None:
    result = optimize_routes(
        _request(
            [
                _stop("pickup", 40.72, -74.0, kind="pickup", pair_id="p1", demand=10),
                _stop("dropoff", 40.73, -74.01, kind="dropoff", pair_id="p1", demand=10),
            ],
            vehicles=[{"id": "v1", "capacity": 5, "shift_start": "00:00", "shift_end": "23:59"}],
        )
    )
    assert {item.stop_id for item in result.unassigned} == {"pickup", "dropoff"}
    assert "capacity" in result.unassigned[0].reason


def test_stop_limit_is_reported() -> None:
    stops = [_stop(f"s{i}", 40.71 + i * 0.001, -74.0) for i in range(3)]
    result = optimize_routes(
        _request(
            stops,
            vehicles=[
                {
                    "id": "v1",
                    "capacity": 10,
                    "max_stops": 2,
                    "shift_start": "00:00",
                    "shift_end": "23:59",
                }
            ],
        )
    )
    assert result.summary.assigned_stops == 2
    assert result.unassigned[0].stop_id == "s2"
    assert "stop limit" in result.unassigned[0].reason


def test_delivery_load_decreases_from_depot() -> None:
    result = optimize_routes(
        _request(
            [
                _stop("first", 40.72, -74.0, demand=2),
                _stop("second", 40.73, -74.01, demand=3),
            ],
            vehicles=[{"id": "v1", "capacity": 10, "shift_start": "00:00", "shift_end": "23:59"}],
        )
    )
    visits = result.routes[0].visits
    assert visits[0].load_after == 3
    assert visits[-1].load_after == 0


def test_result_is_deterministic() -> None:
    request = _request(
        [
            _stop("a", 40.72, -74.0),
            _stop("b", 40.73, -74.01),
            _stop("c", 40.70, -73.99),
        ]
    )
    assert optimize_routes(request).model_dump() == optimize_routes(request).model_dump()


def test_heuristic_is_close_to_brute_force_on_small_instance() -> None:
    offsets = [
        (0.01, 0.0),
        (0.005, 0.009),
        (-0.005, 0.009),
        (-0.01, 0.0),
        (-0.005, -0.009),
        (0.005, -0.009),
    ]
    stops = [
        _stop(f"s{i}", 40.7128 + lat, -74.006 + lng, demand=1)
        for i, (lat, lng) in enumerate(offsets)
    ]
    result = optimize_routes(
        _request(stops, options={"default_service_time_s": 0, "road_factor": 1.3})
    )
    assert result.summary.assigned_stops == 6
    assert result.summary.vehicles_used == 1

    points = [(stop["lat"], stop["lng"]) for stop in stops]
    depot = (DEPOT["lat"], DEPOT["lng"])
    best = float("inf")
    for order in itertools.permutations(range(len(points))):
        path = [depot] + [points[i] for i in order] + [depot]
        distance = sum(_haversine(a[0], a[1], b[0], b[1]) * 1.3 for a, b in zip(path, path[1:]))
        best = min(best, distance)

    assert result.routes[0].distance_m <= best * 1.10


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    from deleezy_optimizer.core.geo import haversine_m

    return haversine_m(lat1, lng1, lat2, lng2)
