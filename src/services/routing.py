"""Vehicle routing optimizer.

The optimizer uses a fast, dependency-free pipeline:

1. Cheapest feasible insertion builds the initial routes.
2. 2-opt improves each route locally.
3. Job re-insertion moves single stops and pickup/dropoff pairs between routes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..core.geo import LatLng, TravelModel
from ..core.time import format_clock
from ..schemas import (
    RouteOptimizationResponse,
    RouteOptimizeRequest,
    RouteOptions,
    RouteSummary,
    StopIn,
    StopVisit,
    UnassignedStop,
    VehicleIn,
    VehicleRoute,
)

EPS = 1e-9
END_OF_DAY = 24 * 3600

_REASON_MESSAGES = {
    "capacity": "capacity would be exceeded on every feasible position",
    "time_window": "no position satisfies the stop time windows",
    "shift": "no position fits within the vehicle shift",
    "max_duration": "no position fits within the vehicle maximum duration",
    "precedence": "pickup/dropoff precedence could not be satisfied",
    "stop_limit": "vehicle stop limit reached",
}
_REASON_PRIORITY = [
    "capacity",
    "time_window",
    "shift",
    "max_duration",
    "precedence",
    "stop_limit",
]


@dataclass
class RouteState:
    vehicle: VehicleIn
    sequence: list[StopIn] = field(default_factory=list)


@dataclass(frozen=True)
class Visit:
    stop: StopIn
    arrival_s: int
    service_start_s: int
    departure_s: int
    load_after: float
    leg_distance_m: float


@dataclass(frozen=True)
class Evaluation:
    feasible: bool
    reason: str | None = None
    distance_m: float = 0.0
    duration_s: int = 0
    finish_s: int = 0
    peak_load: float = 0.0
    visits: tuple[Visit, ...] = ()


@dataclass(frozen=True)
class Insertion:
    route_index: int
    sequence: list[StopIn]
    cost_delta: float


def _location(stop: StopIn) -> LatLng:
    return (stop.lat, stop.lng)


def _pair_demands(stops: list[StopIn]) -> dict[str, float]:
    return {
        stop.pair_id: stop.demand
        for stop in stops
        if stop.kind == "pickup" and stop.pair_id is not None
    }


def _demand(stop: StopIn, pair_demands: dict[str, float]) -> float:
    if stop.kind == "dropoff" and stop.pair_id is not None:
        return pair_demands.get(stop.pair_id, stop.demand)
    return stop.demand


def _service_time(stop: StopIn, options: RouteOptions) -> int:
    if stop.service_time_s is None:
        return options.default_service_time_s
    return stop.service_time_s


def _start_point(vehicle: VehicleIn, depot: LatLng) -> LatLng:
    if vehicle.start is None:
        return depot
    return (vehicle.start.lat, vehicle.start.lng)


def _end_point(vehicle: VehicleIn, depot: LatLng) -> LatLng:
    if vehicle.end is None:
        return depot
    return (vehicle.end.lat, vehicle.end.lng)


def _evaluate_route(
    sequence: list[StopIn],
    vehicle: VehicleIn,
    depot: LatLng,
    options: RouteOptions,
    travel: TravelModel,
    pair_demands: dict[str, float],
) -> Evaluation:
    """Simulate a route and return feasibility, timing, load and distance."""
    if vehicle.max_stops is not None and len(sequence) > vehicle.max_stops:
        return Evaluation(False, "stop_limit")

    start = _start_point(vehicle, depot)
    end = _end_point(vehicle, depot)
    load = sum(_demand(stop, pair_demands) for stop in sequence if stop.kind == "delivery")
    if load > vehicle.capacity + EPS:
        return Evaluation(False, "capacity")

    peak_load = load
    time_s = vehicle.shift_start
    previous = start
    distance_m = 0.0
    visits: list[Visit] = []
    picked_up: set[str] = set()

    for stop in sequence:
        location = _location(stop)
        leg_distance = travel.distance_m(previous, location)
        distance_m += leg_distance
        arrival_s = time_s + travel.travel_time_s(previous, location)

        window = stop.time_window
        if window is not None and arrival_s > window.end:
            return Evaluation(False, "time_window")
        service_start_s = max(arrival_s, window.start) if window is not None else arrival_s
        if service_start_s > vehicle.shift_end:
            return Evaluation(False, "shift")

        departure_s = service_start_s + _service_time(stop, options)
        demand = _demand(stop, pair_demands)

        if stop.kind == "pickup":
            load += demand
            if stop.pair_id is not None:
                picked_up.add(stop.pair_id)
        elif stop.kind == "dropoff":
            if stop.pair_id is not None and stop.pair_id not in picked_up:
                return Evaluation(False, "precedence")
            load -= demand
        else:
            load -= demand

        if load < -EPS or load > vehicle.capacity + EPS:
            return Evaluation(False, "capacity")

        peak_load = max(peak_load, load)
        visits.append(
            Visit(
                stop=stop,
                arrival_s=arrival_s,
                service_start_s=service_start_s,
                departure_s=departure_s,
                load_after=load,
                leg_distance_m=leg_distance,
            )
        )
        time_s = departure_s
        previous = location

    finish_s = time_s + travel.travel_time_s(previous, end)
    if finish_s > vehicle.shift_end:
        return Evaluation(False, "shift")

    duration_s = finish_s - vehicle.shift_start
    if vehicle.max_duration_s is not None and duration_s > vehicle.max_duration_s:
        return Evaluation(False, "max_duration")

    return Evaluation(
        feasible=True,
        distance_m=distance_m,
        duration_s=duration_s,
        finish_s=finish_s,
        peak_load=peak_load,
        visits=tuple(visits),
    )


def _candidate_sequences(sequence: list[StopIn], job: tuple[StopIn, ...]):
    """Yield every sequence created by inserting a job into a route."""
    if len(job) == 1:
        stop = job[0]
        for position in range(len(sequence) + 1):
            yield sequence[:position] + [stop] + sequence[position:]
        return

    pickup, dropoff = job
    length = len(sequence)
    for pickup_position in range(length + 1):
        for dropoff_position in range(pickup_position + 1, length + 2):
            yield (
                sequence[:pickup_position]
                + [pickup]
                + sequence[pickup_position : dropoff_position - 1]
                + [dropoff]
                + sequence[dropoff_position - 1 :]
            )


def _find_best_insertion(
    job: tuple[StopIn, ...],
    routes: list[RouteState],
    depot: LatLng,
    options: RouteOptions,
    travel: TravelModel,
    pair_demands: dict[str, float],
) -> tuple[Insertion | None, Counter[str]]:
    """Return the cheapest feasible insertion for a job, plus failure reasons."""
    best: Insertion | None = None
    reasons: Counter[str] = Counter()

    for route_index, route in enumerate(routes):
        baseline = _evaluate_route(
            route.sequence, route.vehicle, depot, options, travel, pair_demands
        )
        if not baseline.feasible:
            continue
        if (
            route.vehicle.max_stops is not None
            and len(route.sequence) + len(job) > route.vehicle.max_stops
        ):
            reasons["stop_limit"] += 1
            continue

        for candidate in _candidate_sequences(route.sequence, job):
            evaluation = _evaluate_route(
                candidate, route.vehicle, depot, options, travel, pair_demands
            )
            if not evaluation.feasible:
                reasons[evaluation.reason or "unknown"] += 1
                continue
            delta = evaluation.distance_m - baseline.distance_m
            if best is None or delta < best.cost_delta - EPS:
                best = Insertion(route_index=route_index, sequence=candidate, cost_delta=delta)

    return best, reasons


def _describe_failure(reasons: Counter[str]) -> str:
    if not reasons:
        return "no feasible insertion found"
    code = max(
        _REASON_PRIORITY,
        key=lambda reason: (reasons.get(reason, 0), -_REASON_PRIORITY.index(reason)),
    )
    return _REASON_MESSAGES.get(code, "no feasible insertion found")


def _build_jobs(stops: list[StopIn]) -> list[tuple[StopIn, ...]]:
    pairs: dict[str, list[StopIn]] = {}
    jobs: list[tuple[StopIn, ...]] = []
    for stop in stops:
        if stop.kind == "delivery":
            jobs.append((stop,))
        elif stop.pair_id is not None:
            pairs.setdefault(stop.pair_id, []).append(stop)

    for members in pairs.values():
        pickup = next(stop for stop in members if stop.kind == "pickup")
        dropoff = next(stop for stop in members if stop.kind == "dropoff")
        jobs.append((pickup, dropoff))

    jobs.sort(key=_job_sort_key)
    return jobs


def _job_sort_key(job: tuple[StopIn, ...]) -> tuple[int, int, float, str]:
    deadlines = [stop.time_window.end for stop in job if stop.time_window is not None]
    starts = [stop.time_window.start for stop in job if stop.time_window is not None]
    return (
        min(deadlines) if deadlines else END_OF_DAY,
        min(starts) if starts else END_OF_DAY,
        -sum(stop.demand for stop in job),
        job[0].id,
    )


def _total_distance(
    routes: list[RouteState],
    depot: LatLng,
    options: RouteOptions,
    travel: TravelModel,
    pair_demands: dict[str, float],
) -> float:
    total = 0.0
    for route in routes:
        evaluation = _evaluate_route(
            route.sequence, route.vehicle, depot, options, travel, pair_demands
        )
        if evaluation.feasible:
            total += evaluation.distance_m
    return total


def _two_opt_route(
    route: RouteState,
    depot: LatLng,
    options: RouteOptions,
    travel: TravelModel,
    pair_demands: dict[str, float],
) -> bool:
    """Best-improvement 2-opt inside one route. Returns True if it changed."""
    changed = False
    max_moves = max(1, len(route.sequence) * 2)
    for _ in range(max_moves):
        current = _evaluate_route(
            route.sequence, route.vehicle, depot, options, travel, pair_demands
        )
        if not current.feasible:
            break

        best_distance = current.distance_m
        best_sequence: list[StopIn] | None = None
        length = len(route.sequence)
        for i in range(length - 1):
            for j in range(i + 1, length):
                candidate = (
                    route.sequence[:i]
                    + list(reversed(route.sequence[i : j + 1]))
                    + route.sequence[j + 1 :]
                )
                evaluation = _evaluate_route(
                    candidate, route.vehicle, depot, options, travel, pair_demands
                )
                if evaluation.feasible and evaluation.distance_m < best_distance - EPS:
                    best_distance = evaluation.distance_m
                    best_sequence = candidate

        if best_sequence is None:
            break
        route.sequence = best_sequence
        changed = True

    return changed


def _find_job_route(routes: list[RouteState], job: tuple[StopIn, ...]) -> int | None:
    job_ids = {stop.id for stop in job}
    for index, route in enumerate(routes):
        sequence_ids = {stop.id for stop in route.sequence}
        if job_ids.issubset(sequence_ids):
            return index
    return None


def _local_search(
    routes: list[RouteState],
    jobs: list[tuple[StopIn, ...]],
    depot: LatLng,
    options: RouteOptions,
    travel: TravelModel,
    pair_demands: dict[str, float],
) -> None:
    """Alternate 2-opt and global job re-insertion until no improvement."""
    for _ in range(options.max_optimization_rounds):
        improved = False

        for route in routes:
            if _two_opt_route(route, depot, options, travel, pair_demands):
                improved = True

        for job in jobs:
            route_index = _find_job_route(routes, job)
            if route_index is None:
                continue

            base_total = _total_distance(routes, depot, options, travel, pair_demands)
            original_sequence = routes[route_index].sequence
            job_ids = {stop.id for stop in job}
            routes[route_index].sequence = [
                stop for stop in original_sequence if stop.id not in job_ids
            ]

            insertion, _ = _find_best_insertion(job, routes, depot, options, travel, pair_demands)
            if insertion is None:
                routes[route_index].sequence = original_sequence
                continue

            other_index = insertion.route_index
            other_sequence = routes[other_index].sequence if other_index != route_index else None
            routes[other_index].sequence = insertion.sequence

            if _total_distance(routes, depot, options, travel, pair_demands) < base_total - 1e-6:
                improved = True
            else:
                routes[route_index].sequence = original_sequence
                if other_index != route_index and other_sequence is not None:
                    routes[other_index].sequence = other_sequence

        if not improved:
            break


def _build_response(
    request: RouteOptimizeRequest,
    routes: list[RouteState],
    unassigned: dict[str, str],
    algorithm: str,
) -> RouteOptimizationResponse:
    depot = (request.depot.lat, request.depot.lng)
    pair_demands = _pair_demands(request.stops)
    travel = TravelModel(
        speed_kmh=request.options.speed_kmh,
        road_factor=request.options.road_factor,
    )

    route_outputs: list[VehicleRoute] = []
    total_distance = 0.0
    total_duration = 0
    total_service_time = 0

    for route in routes:
        if not route.sequence:
            continue
        evaluation = _evaluate_route(
            route.sequence, route.vehicle, depot, request.options, travel, pair_demands
        )
        visits = [
            StopVisit(
                sequence=index,
                stop_id=visit.stop.id,
                kind=visit.stop.kind,
                arrival_s=visit.arrival_s,
                arrival=format_clock(visit.arrival_s),
                service_start_s=visit.service_start_s,
                service_start=format_clock(visit.service_start_s),
                departure_s=visit.departure_s,
                departure=format_clock(visit.departure_s),
                wait_s=visit.service_start_s - visit.arrival_s,
                load_after=round(visit.load_after, 3),
                distance_from_previous_m=round(visit.leg_distance_m, 1),
            )
            for index, visit in enumerate(evaluation.visits, start=1)
        ]
        service_time = sum(_service_time(stop, request.options) for stop in route.sequence)
        route_outputs.append(
            VehicleRoute(
                vehicle_id=route.vehicle.id,
                visits=visits,
                stop_ids=[stop.id for stop in route.sequence],
                stop_count=len(route.sequence),
                distance_m=round(evaluation.distance_m, 1),
                duration_s=evaluation.duration_s,
                service_time_s=service_time,
                finish_s=evaluation.finish_s,
                finish=format_clock(evaluation.finish_s),
                peak_load=round(evaluation.peak_load, 3),
            )
        )
        total_distance += evaluation.distance_m
        total_duration += evaluation.duration_s
        total_service_time += service_time

    unassigned_out = [
        UnassignedStop(stop_id=stop.id, reason=unassigned[stop.id])
        for stop in request.stops
        if stop.id in unassigned
    ]
    assigned_stops = len(request.stops) - len(unassigned_out)

    return RouteOptimizationResponse(
        routes=route_outputs,
        unassigned=unassigned_out,
        summary=RouteSummary(
            total_stops=len(request.stops),
            assigned_stops=assigned_stops,
            unassigned_stops=len(unassigned_out),
            vehicles_used=len(route_outputs),
            vehicles_available=len(request.vehicles),
            total_distance_m=round(total_distance, 1),
            total_duration_s=total_duration,
            total_service_time_s=total_service_time,
        ),
        algorithm=algorithm,
    )


def optimize_routes(request: RouteOptimizeRequest) -> RouteOptimizationResponse:
    """Optimize delivery routes for the given stops and vehicles."""
    travel = TravelModel(
        speed_kmh=request.options.speed_kmh,
        road_factor=request.options.road_factor,
    )
    depot: LatLng = (request.depot.lat, request.depot.lng)
    pair_demands = _pair_demands(request.stops)
    jobs = _build_jobs(request.stops)
    routes = [RouteState(vehicle=vehicle) for vehicle in request.vehicles]
    unassigned: dict[str, str] = {}

    for job in jobs:
        insertion, reasons = _find_best_insertion(
            job, routes, depot, request.options, travel, pair_demands
        )
        if insertion is None:
            reason = _describe_failure(reasons)
            for stop in job:
                unassigned[stop.id] = reason
        else:
            routes[insertion.route_index].sequence = insertion.sequence

    if request.options.enable_local_search and request.options.max_optimization_rounds > 0:
        _local_search(routes, jobs, depot, request.options, travel, pair_demands)
        algorithm = "cheapest feasible insertion + 2-opt + job re-insertion"
    else:
        algorithm = "cheapest feasible insertion"

    return _build_response(request, routes, unassigned, algorithm)
