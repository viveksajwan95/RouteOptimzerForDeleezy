"""Request and response models for zone breaking and lookup."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from .common import GeoPoint, StrictModel, UnassignedStop


class ZoneStopIn(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    demand: float = Field(default=1.0, ge=0)


class ZoneBreakOptions(StrictModel):
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    max_iterations: int = Field(default=100, ge=1, le=1000)
    restarts: int = Field(default=3, ge=1, le=10)
    max_swap_passes: int = Field(default=2, ge=0, le=20)


class ZoneBreakRequest(StrictModel):
    stops: list[ZoneStopIn] = Field(min_length=1, max_length=5000)
    zone_count: int | None = Field(default=None, ge=1, le=500)
    max_stops_per_zone: int | None = Field(default=None, ge=1)
    max_demand_per_zone: float | None = Field(default=None, gt=0)
    zone_prefix: str = Field(
        default="ZONE", min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$"
    )
    options: ZoneBreakOptions = Field(default_factory=ZoneBreakOptions)

    @model_validator(mode="after")
    def _validate(self) -> ZoneBreakRequest:
        ids = [stop.id for stop in self.stops]
        if len(ids) != len(set(ids)):
            raise ValueError("stop ids must be unique")
        if (
            self.zone_count is None
            and self.max_stops_per_zone is None
            and self.max_demand_per_zone is None
        ):
            raise ValueError(
                "provide zone_count, max_stops_per_zone, max_demand_per_zone, or a combination"
            )
        return self


class BoundingBox(StrictModel):
    min_lat: float
    min_lng: float
    max_lat: float
    max_lng: float


class ZoneOut(StrictModel):
    id: str
    name: str
    stop_ids: list[str]
    stop_count: int
    demand: float
    centroid: GeoPoint
    radius_m: float
    boundary: list[GeoPoint]
    bounds: BoundingBox


class ZoneBreakSummary(StrictModel):
    total_stops: int
    assigned_stops: int
    unassigned_stops: int
    zone_count: int
    total_demand: float
    max_stops: int
    min_stops: int
    balance_ratio: float | None


class ZoneBreakResponse(StrictModel):
    zones: list[ZoneOut]
    unassigned: list[UnassignedStop]
    summary: ZoneBreakSummary
    algorithm: str


class ZoneDefinition(StrictModel):
    id: str
    name: str | None = None
    centroid: GeoPoint | None = None
    boundary: list[GeoPoint] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class ZoneLocateRequest(StrictModel):
    point: GeoPoint
    zones: list[ZoneDefinition] = Field(min_length=1)
    fallback: Literal["nearest_centroid", "none"] = "nearest_centroid"


class ZoneLocateResponse(StrictModel):
    zone_id: str | None
    zone_name: str | None
    matched_by: Literal["polygon", "nearest_centroid"] | None
    inside: bool
    distance_to_centroid_m: float | None
