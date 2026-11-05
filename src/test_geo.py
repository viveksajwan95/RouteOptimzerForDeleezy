"""Geospatial helper tests."""

import math

from deleezy_optimizer.core.geo import (
    TravelModel,
    convex_hull,
    haversine_m,
    point_in_polygon,
)


def test_haversine_one_degree_at_equator() -> None:
    distance = haversine_m(0, 0, 0, 1)
    assert 110_000 < distance < 112_500


def test_haversine_is_symmetric_and_zero_for_same_point() -> None:
    assert haversine_m(40.7128, -74.006, 40.7128, -74.006) == 0
    forward = haversine_m(40.7, -74.0, 40.8, -73.9)
    backward = haversine_m(40.8, -73.9, 40.7, -74.0)
    assert math.isclose(forward, backward, rel_tol=1e-12)


def test_travel_model_applies_road_factor_and_speed() -> None:
    model = TravelModel(speed_kmh=36, road_factor=1.2)
    straight = haversine_m(0, 0, 0, 1)
    assert math.isclose(model.distance_m((0, 0), (0, 1)), straight * 1.2)
    assert model.travel_time_s((0, 0), (0, 1)) == round(straight * 1.2 / 10)


def test_convex_hull_of_square_has_four_corners() -> None:
    points = [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0), (0.5, 0.5)]
    hull = convex_hull(points)
    assert len(hull) == 4
    assert (0.5, 0.5) not in hull


def test_point_in_polygon() -> None:
    square = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
    assert point_in_polygon(0.5, 0.5, square)
    assert not point_in_polygon(2.0, 2.0, square)
    assert not point_in_polygon(0.5, 0.5, square[:2])
