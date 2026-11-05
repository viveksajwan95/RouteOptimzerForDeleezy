# Deleezy Optimization Service

Backend service for **Deleezy**, an on-demand package delivery platform. It solves two
dispatch problems:

- **Route optimization** - assign bookings to couriers and order each courier's stops
  while respecting capacity, time windows, shift hours and pickup/dropoff precedence.
- **Zone breaking** - partition delivery stops into compact, capacity-balanced zones with
  polygon boundaries and a point-lookup endpoint.

The service is a small FastAPI application with no solver or mapping-provider dependency.
It runs offline using a configurable straight-line distance model, so it is easy to deploy,
test and extend with a real road-network provider later.

## Features

- Multi-vehicle vehicle routing with:
  - weight/package capacity per vehicle
  - maximum stops and maximum route duration
  - vehicle shift windows
  - customer time windows with waiting
  - delivery stops loaded at the depot
  - paired pickup/dropoff jobs that must stay on the same route, pickup first
- Returns per-stop arrival, service-start, departure, waiting time, load and leg distance
- Unassigned stops are returned with a human-readable reason instead of silently dropped
- Deterministic output for identical requests
- Capacity-constrained k-means zoning with restarts and balanced swap improvement
- Convex-hull boundaries, centroids, radius and bounding boxes for map display
- Point-in-zone lookup with nearest-centroid fallback
- Interactive OpenAPI docs at `/docs`
- Tests, linting, Docker and GitHub Actions included

## Project layout

```text
Project2/
├── src/deleezy_optimizer/
│   ├── main.py                 # FastAPI app factory and /health
│   ├── demo.py                 # CLI that runs the bundled examples
│   ├── api/                    # HTTP routers
│   ├── core/                   # geo + clock helpers
│   ├── schemas/                # Pydantic request/response models
│   └── services/
│       ├── routing.py          # route optimizer
│       └── zoning.py           # zone breaker and locator
├── examples/                   # ready-to-send request payloads
├── tests/                      # pytest suite
├── Dockerfile
├── Makefile
└── pyproject.toml
```

## Quick start

Requires **Python 3.10+**.

```bash
cd Project2
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

uvicorn deleezy_optimizer.main:app --reload
```

Open <http://127.0.0.1:8000/docs> for the interactive API.

Run the bundled examples without starting the server:

```bash
python -m deleezy_optimizer.demo routes
python -m deleezy_optimizer.demo zones
```

Run the checks:

```bash
ruff check .
ruff format --check .
pytest
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/routes/optimize` | Build optimized routes |
| `POST` | `/api/v1/zones/break` | Partition stops into zones |
| `POST` | `/api/v1/zones/locate` | Find the zone for a coordinate |
| `GET` | `/health` | Liveness check |
| `GET` | `/docs` | Swagger UI |

### 1. Optimize routes

```bash
curl -X POST http://127.0.0.1:8000/api/v1/routes/optimize \
  -H "Content-Type: application/json" \
  --data @examples/route_request.json
```

Minimal request:

```json
{
  "depot": { "lat": 40.7128, "lng": -74.006 },
  "vehicles": [
    {
      "id": "van-1",
      "capacity": 10,
      "max_stops": 12,
      "shift_start": "08:00",
      "shift_end": "18:00"
    }
  ],
  "stops": [
    { "id": "order-1", "lat": 40.72, "lng": -74.0, "kind": "delivery", "demand": 2 },
    {
      "id": "pickup-1",
      "lat": 40.715,
      "lng": -74.002,
      "kind": "pickup",
      "pair_id": "job-1",
      "demand": 1
    },
    {
      "id": "dropoff-1",
      "lat": 40.73,
      "lng": -74.01,
      "kind": "dropoff",
      "pair_id": "job-1",
      "demand": 1
    }
  ]
}
```

Key stop fields:

| Field | Meaning |
| --- | --- |
| `kind` | `delivery` (loaded at depot), `pickup` or `dropoff` |
| `demand` | Load consumed or added; defaults to `1` |
| `service_time_s` | Time spent at the stop; falls back to `options.default_service_time_s` |
| `time_window` | `{ "start": "09:00", "end": "12:00" }`; arrival before `start` waits |
| `pair_id` | Required for pickups and dropoffs; the pair is served by one vehicle in order |

Response shape (abridged):

```json
{
  "routes": [
    {
      "vehicle_id": "van-1",
      "stop_ids": ["pickup-1", "order-1", "dropoff-1"],
      "stop_count": 3,
      "distance_m": 4210.5,
      "duration_s": 7300,
      "finish_s": 36100,
      "finish": "10:01",
      "peak_load": 3.0,
      "visits": [
        {
          "sequence": 1,
          "stop_id": "pickup-1",
          "kind": "pickup",
          "arrival_s": 28851,
          "arrival": "08:00",
          "service_start_s": 32400,
          "service_start": "09:00",
          "departure_s": 32520,
          "departure": "09:02",
          "wait_s": 3549,
          "load_after": 3.0,
          "distance_from_previous_m": 357.2
        }
      ]
    }
  ],
  "unassigned": [],
  "summary": {
    "total_stops": 3,
    "assigned_stops": 3,
    "unassigned_stops": 0,
    "vehicles_used": 1,
    "vehicles_available": 1,
    "total_distance_m": 4210.5,
    "total_duration_s": 7300,
    "total_service_time_s": 360
  },
  "algorithm": "cheapest feasible insertion + 2-opt + job re-insertion"
}
```

`unassigned` contains entries such as:

```json
[
  {
    "stop_id": "order-9",
    "reason": "capacity would be exceeded on every feasible position"
  }
]
```

Possible reasons cover capacity, time windows, shift limits, maximum route duration,
pickup/dropoff precedence and vehicle stop limits.

### 2. Break zones

```bash
curl -X POST http://127.0.0.1:8000/api/v1/zones/break \
  -H "Content-Type: application/json" \
  --data @examples/zone_request.json
```

```json
{
  "stops": [
    { "id": "s1", "lat": 40.70, "lng": -74.00, "demand": 1 },
    { "id": "s2", "lat": 40.71, "lng": -73.99, "demand": 2 }
  ],
  "zone_count": 2,
  "max_stops_per_zone": 20,
  "max_demand_per_zone": 50,
  "zone_prefix": "ZONE"
}
```

`zone_count` is optional when a capacity cap is provided; the minimum feasible number of
zones is derived from the stop-count and demand caps. If an explicit `zone_count` is too
small for the caps, the API returns `422` with the required minimum.

Each zone in the response contains:

- `stop_ids`, `stop_count`, `demand`
- `centroid` (`lat`/`lng`) and `radius_m`
- `boundary` - convex hull as an ordered list of `{ "lat", "lng" }` points
- `bounds` - min/max latitude and longitude

### 3. Locate a point

Pass the `zones` array from `/zones/break` and a coordinate:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/zones/locate \
  -H "Content-Type: application/json" \
  -d '{
    "point": { "lat": 40.713, "lng": -74.005 },
    "zones": [ ...zones from the break response... ],
    "fallback": "nearest_centroid"
  }'
```

The result reports `matched_by` as `polygon` or `nearest_centroid`, plus whether the point
was inside a boundary (`inside`) and its distance to the selected centroid.

## Time and distance model

- Times are seconds since midnight in responses, and may be supplied as `"HH:MM"`,
  `"HH:MM:SS"` or integer seconds. Shifts must end on the same day.
- Distances use the haversine great-circle formula multiplied by `road_factor`
  (default `1.3`). Travel time uses `speed_kmh` (default `30`).
- These two options are configurable per route request. For production accuracy, replace
  `TravelModel` with a road-network travel-time matrix (for example OSRM, Valhalla or a
  commercial provider). The optimizer consumes distances/times through the same model.

## Algorithms

### Route optimizer

1. Build jobs: each delivery is a one-stop job; each pickup/dropoff pair is a two-stop job.
2. Sort jobs by tightest deadline, window start and demand.
3. Insert each job at the cheapest feasible position across all vehicles. Pair insertion
   checks every pickup/dropoff index combination so the pair stays ordered.
4. Improve with best-improvement 2-opt inside each route.
5. Re-insert every job globally to escape a poor initial assignment, accepting only strict
   distance improvements. Steps 4 and 5 repeat until convergence or the round limit.
6. Report any job that has no feasible position, with the dominant failure reason.

This is a heuristic, not a branch-and-bound solver. It is designed for fast dispatch
responses and can be replaced or augmented with OR-Tools if exact solutions are required.

### Zone breaker

1. Determine the minimum zone count from the supplied caps.
2. Project stops into a local metre-based plane.
3. Run capacity-constrained k-means++ with multiple seeded restarts.
4. Assign high-regret points first, then the nearest zone with remaining capacity.
5. Improve balance with capacity-safe swaps between zones.
6. Return centroids and convex-hull boundaries.

All random behaviour is seeded, so identical requests produce identical zones.

## Configuration

| Environment variable | Default | Description |
| --- | --- | --- |
| `DELEEZY_CORS_ORIGINS` | `*` | Comma-separated allowed browser origins |

Set a restricted origin list in production, for example:

```bash
export DELEEZY_CORS_ORIGINS="https://app.deleezy.com,https://admin.deleezy.com"
```

## Docker

```bash
docker build -t deleezy-optimizer .
docker run --rm -p 8000:8000 deleezy-optimizer
```

## Performance notes

- Pure Python, no native solver. Typical dispatch batches of a few hundred stops complete
  quickly with local search enabled.
- For very large batches, set `"enable_local_search": false` or lower
  `max_optimization_rounds` for a faster first solution.
- Zone swap improvement is skipped above 1,500 stops; k-means itself scales further.
- Capacity assignment is a heuristic. Stop-count capacities are guaranteed when the total
  capacity is sufficient; demand capacity can occasionally leave a stop unassigned if the
  individual demands cannot be packed into the remaining zone capacity. Tighten the caps or
  increase `zone_count` in that case.

## Uploading to Git

This folder is already initialized as a Git repository with an initial commit. Create an
empty repository on GitHub, then connect and push:

```bash
cd Project2
git remote add origin https://github.com/<your-account>/deleezy-optimizer.git
git push -u origin main
```

Or, with the GitHub CLI:

```bash
gh repo create deleezy-optimizer --private --source=. --remote=origin --push
```

If you copy the files elsewhere without the `.git` directory, initialize a new repository:

```bash
git init -b main
git add .
git commit -m "Add Deleezy route optimization and zone breaking service"
```

The included `.gitignore` excludes virtual environments, caches and local env files.
GitHub Actions runs linting and tests on Python 3.10 and 3.12.

## License

MIT. See [LICENSE](LICENSE).
