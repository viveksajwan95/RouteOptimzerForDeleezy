"""Route optimization endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..schemas import RouteOptimizationResponse, RouteOptimizeRequest
from ..services.routing import optimize_routes

router = APIRouter(prefix="/routes", tags=["routes"])


@router.post(
    "/optimize",
    response_model=RouteOptimizationResponse,
    summary="Optimize vehicle routes",
    description=(
        "Build feasible routes for a fleet, respecting capacity, time windows, "
        "shift limits and pickup/dropoff precedence."
    ),
)
def optimize(request: RouteOptimizeRequest) -> RouteOptimizationResponse:
    try:
        return optimize_routes(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
