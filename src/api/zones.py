"""Zone breaking and lookup endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..schemas import (
    ZoneBreakRequest,
    ZoneBreakResponse,
    ZoneLocateRequest,
    ZoneLocateResponse,
)
from ..services.zoning import ZoningError, break_zones, locate_zone

router = APIRouter(prefix="/zones", tags=["zones"])


@router.post(
    "/break",
    response_model=ZoneBreakResponse,
    summary="Partition stops into delivery zones",
    description=(
        "Group stops into compact zones while enforcing optional stop-count and "
        "demand caps. Each zone includes a centroid and convex-hull boundary."
    ),
)
def break_into_zones(request: ZoneBreakRequest) -> ZoneBreakResponse:
    try:
        return break_zones(request)
    except ZoningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/locate",
    response_model=ZoneLocateResponse,
    summary="Find the zone for a point",
    description="Use the boundaries returned by /zones/break to resolve a coordinate.",
)
def locate(request: ZoneLocateRequest) -> ZoneLocateResponse:
    return locate_zone(request)
