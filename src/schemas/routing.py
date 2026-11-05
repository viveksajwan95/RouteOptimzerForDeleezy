"""Request and response models for route optimization."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import ClockSeconds, GeoPoint, StrictModel, UnassignedStop

StopKind = Literal["delivery", "pickup", "dropoff"]


class TimeWindow(StrictModel):
    start: ClockSeconds
    end: ClockSeconds

    @model_validator(mode="after")
    def _check_order(self) -> TimeWindow:
        if self.end < self.start:
            raise ValueError("time_window.end must be greater than or equal to start")
        return self


class StopIn(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    kind: StopKind = "delivery"
    demand: float = Field(default=1.0, ge=0)
    service_time_s: int | None = Field(default=None, ge=0, le=24 * 3600)
    time_window: TimeWindow | None = None
    pair_id: str | None = Field(default=None, min_length=1, max_length=100)


class VehicleIn(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    capacity: float = Field(default=20.0, gt=0)
    max_stops: int | None = Field(default=None, ge=1)
    max_duration_s: int | None = Field(default=None, ge=1)
    shift_start: ClockSeconds = 8 * 3600
    shift_end: ClockSeconds = 18 * 3600
    start: GeoPoint | None = None
    end: GeoPoint | None = None

    @model_validator(mode="after")
    def _check_shift(self) -> VehicleIn:
        if self.shift_end <= self.shift_start:
            raise ValueError("shift_end must be greater than shift_start")
        return self


class RouteOptions(StrictModel):
    speed_kmh: float = Field(default=30.0, gt=0, le=120)
    road_factor: float = Field(default=1.3, ge=1.0, le=3.0)
    default_service_time_s: int = Field(default=120, ge=0, le=7200)
    enable_local_search: bool = True
    max_optimization_rounds: int = Field(default=25, ge=0, le=1000)


class RouteOptimizeRequest(StrictModel):
    depot: GeoPoint
    vehicles: list[VehicleIn] = Field(min_length=1, max_length=200)
    stops: list[StopIn] = Field(min_length=1, max_length=1000)
    options: RouteOptions = Field(default_factory=RouteOptions)

    @model_validator(mode="after")
    def _validate_ids_and_pairs(self) -> RouteOptimizeRequest:
        stop_ids = [stop.id for stop in self.stops]
        if len(stop_ids) != len(set(stop_ids)):
            raise ValueError("stop ids must be unique")

        vehicle_ids = [vehicle.id for vehicle in self.vehicles]
        if len(vehicle_ids) != len(set(vehicle_ids)):
            raise ValueError("vehicle ids must be unique")

        pairs: dict[str, list[StopIn]] = {}
        for stop in self.stops:
            if stop.kind in ("pickup", "dropoff"):
                if stop.pair_id is None:
                    raise ValueError(f"stop '{stop.id}' requires pair_id for kind '{stop.kind}'")
                pairs.setdefault(stop.pair_id, []).append(stop)
            elif stop.pair_id is not None:
                raise ValueError(f"stop '{stop.id}' has pair_id but kind is 'delivery'")

        for pair_id, members in pairs.items():
            if len(members) != 2:
                raise ValueError(f"pair '{pair_id}' needs exactly one pickup and one dropoff")
            kinds = {member.kind for member in members}
            if kinds != {"pickup", "dropoff"}:
                raise ValueError(f"pair '{pair_id}' must contain one pickup and one dropoff")
        return self


class StopVisit(StrictModel):
    sequence: int
    stop_id: str
    kind: StopKind
    arrival_s: int
    arrival: str
    service_start_s: int
    service_start: str
    departure_s: int
    departure: str
    wait_s: int
    load_after: float
    distance_from_previous_m: float


class VehicleRoute(StrictModel):
    vehicle_id: str
    visits: list[StopVisit]
    stop_ids: list[str]
    stop_count: int
    distance_m: float
    duration_s: int
    service_time_s: int
    finish_s: int
    finish: str
    peak_load: float


class RouteSummary(StrictModel):
    total_stops: int
    assigned_stops: int
    unassigned_stops: int
    vehicles_used: int
    vehicles_available: int
    total_distance_m: float
    total_duration_s: int
    total_service_time_s: int


class RouteOptimizationResponse(StrictModel):
    routes: list[VehicleRoute]
    unassigned: list[UnassignedStop]
    summary: RouteSummary
    algorithm: str
