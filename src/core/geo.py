"""Geospatial primitives shared by routing and zoning."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_008.8

LatLng = tuple[float, float]


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, a)))


@dataclass(frozen=True)
class TravelModel:
    """Straight-line distance adjusted for road detours, plus a travel time."""

    speed_kmh: float = 30.0
    road_factor: float = 1.3

    @property
    def speed_mps(self) -> float:
        return self.speed_kmh * 1000.0 / 3600.0

    def distance_m(self, a: LatLng, b: LatLng) -> float:
        return haversine_m(a[0], a[1], b[0], b[1]) * self.road_factor

    def travel_time_s(self, a: LatLng, b: LatLng) -> int:
        return int(round(self.distance_m(a, b) / self.speed_mps))


def path_distance_m(points: Sequence[LatLng], model: TravelModel) -> float:
    """Road-adjusted length of a polyline."""
    return sum(model.distance_m(a, b) for a, b in zip(points, points[1:]))


def project_to_local(points: Sequence[LatLng]) -> list[tuple[float, float]]:
    """Project lat/lng points to a local planar frame in metres."""
    if not points:
        return []
    mean_lat = sum(point[0] for point in points) / len(points)
    cos_lat = math.cos(math.radians(mean_lat))
    return [
        (
            math.radians(lng) * EARTH_RADIUS_M * cos_lat,
            math.radians(lat) * EARTH_RADIUS_M,
        )
        for lat, lng in points
    ]


def convex_hull(points: Sequence[LatLng]) -> list[LatLng]:
    """Monotonic-chain convex hull. Collinear duplicate points are removed."""
    unique = sorted({(round(lng, 7), round(lat, 7)) for lat, lng in points})
    if len(unique) <= 2:
        return [(lat, lng) for lng, lat in unique]

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)

    hull = lower[:-1] + upper[:-1]
    return [(lat, lng) for lng, lat in hull]


def _point_on_segment(
    lat: float,
    lng: float,
    a: LatLng,
    b: LatLng,
    tolerance: float = 1e-10,
) -> bool:
    a_lat, a_lng = a
    b_lat, b_lng = b
    cross = (lng - a_lng) * (b_lat - a_lat) - (lat - a_lat) * (b_lng - a_lng)
    if abs(cross) > tolerance:
        return False
    dot = (lng - a_lng) * (b_lng - a_lng) + (lat - a_lat) * (b_lat - a_lat)
    if dot < -tolerance:
        return False
    length_sq = (b_lng - a_lng) ** 2 + (b_lat - a_lat) ** 2
    return dot <= length_sq + tolerance


def point_in_polygon(lat: float, lng: float, polygon: Sequence[LatLng]) -> bool:
    """Ray-casting test for a lat/lng point inside or on a polygon boundary."""
    if len(polygon) < 3:
        return False

    for i in range(len(polygon)):
        if _point_on_segment(lat, lng, polygon[i], polygon[(i + 1) % len(polygon)]):
            return True

    inside = False
    j = len(polygon) - 1
    for i, (lat_i, lng_i) in enumerate(polygon):
        lat_j, lng_j = polygon[j]
        crosses = (lng_i > lng) != (lng_j > lng)
        if crosses:
            edge_lat = (lat_j - lat_i) * (lng - lng_i) / (lng_j - lng_i) + lat_i
            if lat < edge_lat:
                inside = not inside
        j = i
    return inside
