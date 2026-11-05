"""Zone breaking and locator tests."""

from __future__ import annotations

import pytest

from deleezy_optimizer.core.geo import point_in_polygon
from deleezy_optimizer.schemas import ZoneBreakRequest, ZoneLocateRequest
from deleezy_optimizer.services.zoning import ZoningError, break_zones, locate_zone


def _grid(count: int = 20, demand_cycle: tuple[int, ...] = (1, 2, 3, 4)) -> list[dict]:
    return [
        {
            "id": f"s{i}",
            "lat": 40.70 + (i % 5) * 0.004,
            "lng": -74.00 + (i // 5) * 0.004,
            "demand": demand_cycle[i % len(demand_cycle)],
        }
        for i in range(count)
    ]


def _request(**kwargs) -> ZoneBreakRequest:
    payload = {"stops": _grid(), **kwargs}
    return ZoneBreakRequest.model_validate(payload)


def test_zones_cover_every_stop_and_respect_stop_caps() -> None:
    request = _request(zone_count=4, max_stops_per_zone=6, max_demand_per_zone=15)
    result = break_zones(request)

    assert result.summary.assigned_stops == 20
    assert result.summary.unassigned_stops == 0
    assert len(result.zones) == 4
    assert all(zone.stop_count <= 6 for zone in result.zones)
    assert all(zone.demand <= 15 for zone in result.zones)
    all_ids = [stop_id for zone in result.zones for stop_id in zone.stop_ids]
    assert len(all_ids) == 20
    assert len(set(all_ids)) == 20


def test_missing_zone_count_is_derived_from_stop_capacity() -> None:
    request = ZoneBreakRequest.model_validate({"stops": _grid(23), "max_stops_per_zone": 5})
    result = break_zones(request)
    assert len(result.zones) == 5
    assert all(zone.stop_count <= 5 for zone in result.zones)
    assert result.summary.unassigned_stops == 0


def test_missing_zone_count_is_derived_from_demand_capacity() -> None:
    stops = [{"id": f"s{i}", "lat": 40.7 + i * 0.001, "lng": -74.0, "demand": 2} for i in range(10)]
    request = ZoneBreakRequest.model_validate({"stops": stops, "max_demand_per_zone": 10})
    result = break_zones(request)
    assert len(result.zones) == 2
    assert all(zone.demand <= 10 for zone in result.zones)
    assert result.summary.assigned_stops == 10


def test_conflicting_zone_count_raises() -> None:
    request = _request(zone_count=2, max_stops_per_zone=5)
    with pytest.raises(ZoningError, match="at least 4"):
        break_zones(request)


def test_zone_count_cannot_exceed_stops() -> None:
    request = ZoneBreakRequest.model_validate({"stops": _grid(3), "zone_count": 5})
    with pytest.raises(ZoningError, match="greater than"):
        break_zones(request)


def test_zone_boundaries_contain_their_stops() -> None:
    result = break_zones(_request(zone_count=4, max_stops_per_zone=6))
    lookup = {stop["id"]: (stop["lat"], stop["lng"]) for stop in _grid()}
    for zone in result.zones:
        polygon = [(point.lat, point.lng) for point in zone.boundary]
        assert len(polygon) >= 3
        for stop_id in zone.stop_ids:
            lat, lng = lookup[stop_id]
            assert point_in_polygon(lat, lng, polygon), (zone.id, stop_id)
        assert point_in_polygon(zone.centroid.lat, zone.centroid.lng, polygon)


def test_breaking_is_deterministic() -> None:
    request = _request(zone_count=4, max_stops_per_zone=6)
    first = break_zones(request)
    second = break_zones(request)
    assert first.model_dump() == second.model_dump()


def test_locator_matches_polygon_and_nearest_fallback() -> None:
    result = break_zones(_request(zone_count=4, max_stops_per_zone=6))
    zones = [zone.model_dump() for zone in result.zones]

    inside = locate_zone(
        ZoneLocateRequest.model_validate(
            {"point": result.zones[0].centroid.model_dump(), "zones": zones}
        )
    )
    assert inside.inside is True
    assert inside.matched_by == "polygon"
    assert inside.zone_id is not None

    outside = locate_zone(
        ZoneLocateRequest.model_validate({"point": {"lat": 41.0, "lng": -73.0}, "zones": zones})
    )
    assert outside.inside is False
    assert outside.matched_by == "nearest_centroid"
    assert outside.zone_id is not None
    assert outside.distance_to_centroid_m is not None

    no_fallback = locate_zone(
        ZoneLocateRequest.model_validate(
            {
                "point": {"lat": 41.0, "lng": -73.0},
                "zones": zones,
                "fallback": "none",
            }
        )
    )
    assert no_fallback.zone_id is None
    assert no_fallback.matched_by is None


def test_demand_cap_is_respected() -> None:
    result = break_zones(_request(zone_count=4, max_stops_per_zone=8, max_demand_per_zone=16))
    assert result.summary.assigned_stops == 20
    assert all(zone.demand <= 16 for zone in result.zones)
