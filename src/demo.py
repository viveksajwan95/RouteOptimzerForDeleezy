"""Run the optimizer against the bundled example payloads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .schemas import RouteOptimizeRequest, ZoneBreakRequest
from .services.routing import optimize_routes
from .services.zoning import break_zones

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples"


def _load(name: str) -> dict:
    return json.loads((EXAMPLE_DIR / name).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Deleezy optimizer demo")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["routes", "zones"],
        default="routes",
        help="which example to run",
    )
    args = parser.parse_args()

    if args.command == "routes":
        request = RouteOptimizeRequest.model_validate(_load("route_request.json"))
        result = optimize_routes(request)
    else:
        request = ZoneBreakRequest.model_validate(_load("zone_request.json"))
        result = break_zones(request)

    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    main()
