"""Shared request/response models."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from ..core.time import parse_clock_seconds

ClockSeconds = Annotated[int, BeforeValidator(parse_clock_seconds)]


class StrictModel(BaseModel):
    """Reject unknown fields so client typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


class GeoPoint(BaseModel):
    """A WGS84 coordinate."""

    lat: float = Field(ge=-90, le=90, description="Latitude")
    lng: float = Field(ge=-180, le=180, description="Longitude")

    model_config = ConfigDict(extra="forbid")


class UnassignedStop(BaseModel):
    stop_id: str
    reason: str
