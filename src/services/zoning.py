"""Delivery zone breaking and point lookup.

Stops are grouped into compact, capacity-aware zones with a capacitated
k-means heuristic. Each zone is returned with a convex-hull boundary and a
centroid that can be used for dispatch and map display.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ..core.geo import (
    LatLng,
    convex_hull,
    haversine_m,
    point_in_polygon,
    project_to_local,
)
from ..schemas import (
    BoundingBox,
    UnassignedStop,
    ZoneBreakRequest,
    ZoneBreakResponse,
    ZoneBreakSummary,
    ZoneDefinition,
    ZoneLocateRequest,
    ZoneLocateResponse,
    ZoneOut,
    ZoneStopIn,
)

EPS = 1e-9
INF = float("inf")
MAX_SWAP_POINTS = 1500


class ZoningError(ValueError):
    """Raised when a zone-breaking request cannot be satisfied."""


@dataclass(frozen=True)
class _Point:
    stop: ZoneStopIn
    x: float
    y: float


@dataclass(frozen=True)
class _Solution:
    assignment: list[int]
    centroids: list[tuple[float, float]]
    leftovers: list[int]
    sse: float


def _dist2(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _make_points(stops: list[ZoneStopIn]) -> list[_Point]:
    projected = project_to_local([(stop.lat, stop.lng) for stop in stops])
    return [_Point(stop=stop, x=point[0], y=point[1]) for stop, point in zip(stops, projected)]


def _determine_zone_count(request: ZoneBreakRequest) -> int:
    count = len(request.stops)
    total_demand = sum(stop.demand for stop in request.stops)

    required = 1
    if request.max_stops_per_zone is not None:
        required = max(required, math.ceil(count / request.max_stops_per_zone))
    if request.max_demand_per_zone is not None:
        required = max(required, math.ceil(total_demand / request.max_demand_per_zone - 1e-9))

    if required > count:
        raise ZoningError(
            f"capacity constraints require at least {required} zones for {count} stops"
        )

    if request.zone_count is None:
        return required

    if request.zone_count > count:
        raise ZoningError("zone_count cannot be greater than the number of stops")
    if request.zone_count < required:
        raise ZoningError(
            f"zone_count={request.zone_count} cannot satisfy the capacity constraints; "
            f"at least {required} zones are required"
        )
    return request.zone_count


def _kmeans_plus_plus(
    points: list[_Point], k: int, rng: random.Random
) -> list[tuple[float, float]]:
    first = rng.randrange(len(points))
    centroids = [(points[first].x, points[first].y)]
    closest = [_dist2((point.x, point.y), centroids[0]) for point in points]

    while len(centroids) < k:
        total = sum(closest)
        if total <= EPS:
            index = rng.randrange(len(points))
        else:
            target = rng.random() * total
            cumulative = 0.0
            index = len(points) - 1
            for i, weight in enumerate(closest):
                cumulative += weight
                if cumulative >= target:
                    index = i
                    break
        centroid = (points[index].x, points[index].y)
        centroids.append(centroid)
        closest = [
            min(weight, _dist2((point.x, point.y), centroid))
            for point, weight in zip(points, closest)
        ]
    return centroids


def _assign(
    points: list[_Point],
    centroids: list[tuple[float, float]],
    max_stops: int | None,
    max_demand: float | None,
) -> tuple[list[int], list[int], list[float], list[int], float]:
    k = len(centroids)
    distances = [
        [_dist2((point.x, point.y), centroid) for centroid in centroids] for point in points
    ]

    urgency: list[tuple[float, int]] = []
    for index, row in enumerate(distances):
        ordered = sorted(row)
        regret = ordered[1] - ordered[0] if k > 1 else 0.0
        urgency.append((-regret, index))
    urgency.sort()

    assignment = [-1] * len(points)
    counts = [0] * k
    demand_loads = [0.0] * k
    leftovers: list[int] = []

    for _, index in urgency:
        point = points[index]
        placed = False
        for zone in sorted(range(k), key=lambda candidate: distances[index][candidate]):
            if max_stops is not None and counts[zone] >= max_stops:
                continue
            if max_demand is not None and demand_loads[zone] + point.stop.demand > max_demand + EPS:
                continue
            assignment[index] = zone
            counts[zone] += 1
            demand_loads[zone] += point.stop.demand
            placed = True
            break
        if not placed:
            leftovers.append(index)

    sse = sum(distances[index][zone] for index, zone in enumerate(assignment) if zone >= 0)
    return assignment, counts, demand_loads, leftovers, sse


def _update_centroids(
    points: list[_Point],
    assignment: list[int],
    previous: list[tuple[float, float]],
    k: int,
) -> list[tuple[float, float]]:
    sums = [[0.0, 0.0, 0] for _ in range(k)]
    for point, zone in zip(points, assignment):
        if zone < 0:
            continue
        sums[zone][0] += point.x
        sums[zone][1] += point.y
        sums[zone][2] += 1

    centroids: list[tuple[float, float]] = []
    for zone in range(k):
        x_sum, y_sum, count = sums[zone]
        if count == 0:
            centroids.append(previous[zone])
        else:
            centroids.append((x_sum / count, y_sum / count))
    return centroids


def _solution_sse(
    points: list[_Point], assignment: list[int], centroids: list[tuple[float, float]]
) -> float:
    return sum(
        _dist2((point.x, point.y), centroids[zone])
        for point, zone in zip(points, assignment)
        if zone >= 0
    )


def _run_kmeans(
    points: list[_Point],
    k: int,
    seed: int,
    max_iterations: int,
    max_stops: int | None,
    max_demand: float | None,
) -> _Solution:
    rng = random.Random(seed)
    centroids = _kmeans_plus_plus(points, k, rng)
    previous_assignment: list[int] | None = None
    assignment: list[int] = []
    leftovers: list[int] = []

    for _ in range(max_iterations):
        assignment, _, _, leftovers, sse = _assign(points, centroids, max_stops, max_demand)
        new_centroids = _update_centroids(points, assignment, centroids, k)
        movement = max(math.sqrt(_dist2(old, new)) for old, new in zip(centroids, new_centroids))
        centroids = new_centroids
        if previous_assignment == assignment and movement < 0.5:
            break
        previous_assignment = assignment

    assignment, _, _, leftovers, sse = _assign(points, centroids, max_stops, max_demand)
    return _Solution(
        assignment=assignment,
        centroids=centroids,
        leftovers=leftovers,
        sse=sse,
    )


def _ensure_non_empty(points: list[_Point], solution: _Solution, k: int) -> _Solution:
    assignment = solution.assignment[:]
    counts = [assignment.count(zone) for zone in range(k)]
    if len(points) < k:
        return solution

    for empty_zone in range(k):
        if counts[empty_zone] > 0:
            continue
        donor = max(range(k), key=lambda zone: counts[zone])
        if counts[donor] <= 1:
            break
        donor_indices = [i for i, zone in enumerate(assignment) if zone == donor]
        centroid = solution.centroids[donor]
        moved = max(
            donor_indices,
            key=lambda i: _dist2((points[i].x, points[i].y), centroid),
        )
        assignment[moved] = empty_zone
        counts[donor] -= 1
        counts[empty_zone] += 1

    centroids = _update_centroids(points, assignment, solution.centroids, k)
    return _Solution(
        assignment=assignment,
        centroids=centroids,
        leftovers=[i for i, zone in enumerate(assignment) if zone < 0],
        sse=_solution_sse(points, assignment, centroids),
    )


def _improve_swaps(
    points: list[_Point],
    solution: _Solution,
    k: int,
    max_demand: float | None,
    max_passes: int,
) -> _Solution:
    if max_passes <= 0 or len(points) > MAX_SWAP_POINTS:
        return solution

    assignment = solution.assignment[:]
    centroids = solution.centroids
    demand_loads = [
        sum(points[i].stop.demand for i, zone in enumerate(assignment) if zone == current)
        for current in range(k)
    ]

    for _ in range(max_passes):
        best: tuple[float, int, int, int, int] | None = None
        for zone_a in range(k):
            indices_a = [i for i, zone in enumerate(assignment) if zone == zone_a]
            for zone_b in range(zone_a + 1, k):
                indices_b = [i for i, zone in enumerate(assignment) if zone == zone_b]
                for i in indices_a:
                    for j in indices_b:
                        demand_i = points[i].stop.demand
                        demand_j = points[j].stop.demand
                        if max_demand is not None:
                            if demand_loads[zone_a] - demand_i + demand_j > max_demand + EPS:
                                continue
                            if demand_loads[zone_b] - demand_j + demand_i > max_demand + EPS:
                                continue
                        point_i = (points[i].x, points[i].y)
                        point_j = (points[j].x, points[j].y)
                        delta = (
                            _dist2(point_i, centroids[zone_b])
                            + _dist2(point_j, centroids[zone_a])
                            - _dist2(point_i, centroids[zone_a])
                            - _dist2(point_j, centroids[zone_b])
                        )
                        if delta < -1e-6 and (best is None or delta < best[0]):
                            best = (delta, i, j, zone_a, zone_b)

        if best is None:
            break
        _, i, j, zone_a, zone_b = best
        assignment[i], assignment[j] = zone_b, zone_a
        demand_loads[zone_a] += points[j].stop.demand - points[i].stop.demand
        demand_loads[zone_b] += points[i].stop.demand - points[j].stop.demand

    centroids = _update_centroids(points, assignment, centroids, k)
    return _Solution(
        assignment=assignment,
        centroids=centroids,
        leftovers=[i for i, zone in enumerate(assignment) if zone < 0],
        sse=_solution_sse(points, assignment, centroids),
    )


def _zone_centroid(stops: list[ZoneStopIn]) -> tuple[float, float]:
    return (
        sum(stop.lat for stop in stops) / len(stops),
        sum(stop.lng for stop in stops) / len(stops),
    )


def _hull(stops: list[ZoneStopIn]) -> list[LatLng]:
    return convex_hull([(stop.lat, stop.lng) for stop in stops])


def _build_zone(
    zone_index: int,
    request: ZoneBreakRequest,
    points: list[_Point],
    assignment: list[int],
) -> ZoneOut:
    stops = [points[i].stop for i, zone in enumerate(assignment) if zone == zone_index]
    centroid_lat, centroid_lng = _zone_centroid(stops)
    boundary = _hull(stops)
    radius = max(haversine_m(centroid_lat, centroid_lng, stop.lat, stop.lng) for stop in stops)
    return ZoneOut(
        id=f"{request.zone_prefix}-{zone_index + 1}",
        name=f"Zone {zone_index + 1}",
        stop_ids=[stop.id for stop in stops],
        stop_count=len(stops),
        demand=round(sum(stop.demand for stop in stops), 3),
        centroid={"lat": round(centroid_lat, 7), "lng": round(centroid_lng, 7)},
        radius_m=round(radius, 1),
        boundary=[{"lat": round(lat, 7), "lng": round(lng, 7)} for lat, lng in boundary],
        bounds=BoundingBox(
            min_lat=min(stop.lat for stop in stops),
            min_lng=min(stop.lng for stop in stops),
            max_lat=max(stop.lat for stop in stops),
            max_lng=max(stop.lng for stop in stops),
        ),
    )


def break_zones(request: ZoneBreakRequest) -> ZoneBreakResponse:
    """Partition stops into compact zones while respecting capacity limits."""
    k = _determine_zone_count(request)
    points = _make_points(request.stops)

    best: _Solution | None = None
    for restart in range(request.options.restarts):
        solution = _run_kmeans(
            points=points,
            k=k,
            seed=request.options.seed + restart,
            max_iterations=request.options.max_iterations,
            max_stops=request.max_stops_per_zone,
            max_demand=request.max_demand_per_zone,
        )
        solution = _ensure_non_empty(points, solution, k)
        if best is None:
            best = solution
            continue

        best_spread = max(best.assignment.count(zone) for zone in range(k)) - min(
            best.assignment.count(zone) for zone in range(k)
        )
        solution_spread = max(solution.assignment.count(zone) for zone in range(k)) - min(
            solution.assignment.count(zone) for zone in range(k)
        )
        if (
            len(solution.leftovers),
            round(solution.sse, 3),
            solution_spread,
        ) < (
            len(best.leftovers),
            round(best.sse, 3),
            best_spread,
        ):
            best = solution

    assert best is not None
    best = _improve_swaps(
        points,
        best,
        k,
        request.max_demand_per_zone,
        request.options.max_swap_passes,
    )

    zones = [
        _build_zone(zone_index, request, points, best.assignment)
        for zone_index in range(k)
        if any(zone == zone_index for zone in best.assignment)
    ]

    unassigned = [
        UnassignedStop(
            stop_id=points[index].stop.id,
            reason="zone capacity constraints prevented assignment",
        )
        for index in best.leftovers
    ]
    counts = [zone.stop_count for zone in zones]
    total_demand = sum(stop.demand for stop in request.stops)
    max_stops = max(counts) if counts else 0
    min_stops = min(counts) if counts else 0

    return ZoneBreakResponse(
        zones=zones,
        unassigned=unassigned,
        summary=ZoneBreakSummary(
            total_stops=len(request.stops),
            assigned_stops=len(request.stops) - len(unassigned),
            unassigned_stops=len(unassigned),
            zone_count=len(zones),
            total_demand=round(total_demand, 3),
            max_stops=max_stops,
            min_stops=min_stops,
            balance_ratio=round(max_stops / min_stops, 2) if min_stops else None,
        ),
        algorithm="capacity-constrained k-means + convex hull",
    )


def _definition_centroid(zone: ZoneDefinition) -> LatLng | None:
    if zone.centroid is not None:
        return (zone.centroid.lat, zone.centroid.lng)
    if zone.boundary:
        return _zone_centroid(
            [ZoneStopIn(id="boundary", lat=point.lat, lng=point.lng) for point in zone.boundary]
        )
    return None


def locate_zone(request: ZoneLocateRequest) -> ZoneLocateResponse:
    """Find the zone containing a point, with an optional nearest fallback."""
    point = (request.point.lat, request.point.lng)
    containing: list[tuple[float, ZoneDefinition, LatLng | None]] = []

    for zone in request.zones:
        polygon = [(point.lat, point.lng) for point in zone.boundary]
        if not point_in_polygon(point[0], point[1], polygon):
            continue
        centroid = _definition_centroid(zone)
        distance = (
            haversine_m(point[0], point[1], centroid[0], centroid[1])
            if centroid is not None
            else INF
        )
        containing.append((distance, zone, centroid))

    if containing:
        distance, zone, _ = min(containing, key=lambda item: (item[0], item[1].id))
        return ZoneLocateResponse(
            zone_id=zone.id,
            zone_name=zone.name,
            matched_by="polygon",
            inside=True,
            distance_to_centroid_m=(None if math.isinf(distance) else round(distance, 1)),
        )

    if request.fallback == "none":
        return ZoneLocateResponse(
            zone_id=None,
            zone_name=None,
            matched_by=None,
            inside=False,
            distance_to_centroid_m=None,
        )

    nearest: tuple[float, ZoneDefinition] | None = None
    for zone in request.zones:
        centroid = _definition_centroid(zone)
        if centroid is None:
            continue
        distance = haversine_m(point[0], point[1], centroid[0], centroid[1])
        if nearest is None or (distance, zone.id) < (nearest[0], nearest[1].id):
            nearest = (distance, zone)

    if nearest is None:
        return ZoneLocateResponse(
            zone_id=None,
            zone_name=None,
            matched_by=None,
            inside=False,
            distance_to_centroid_m=None,
        )

    distance, zone = nearest
    return ZoneLocateResponse(
        zone_id=zone.id,
        zone_name=zone.name,
        matched_by="nearest_centroid",
        inside=False,
        distance_to_centroid_m=round(distance, 1),
    )
