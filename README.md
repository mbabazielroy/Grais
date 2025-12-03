# GRAIS – Global Resource Allocation Intelligence System

Production-ready baseline for predicting shortages, optimizing allocations, and prioritizing humanitarian response across global and regional (Ontario) contexts.

## Features
- Dual modes: `global` (countries) and `regional` (Ontario cities/districts).
- Synthetic data generator so the stack runs end-to-end without external feeds.
- ML shortage predictor with optional time-series forecaster (Prophet if available, linear fallback).
- OR-Tools allocation optimizer with distance/budget constraints and multi-resource support.
- LLM-backed (or local heuristic) prioritization layer with explanations.
- FastAPI backend + lightweight dashboard (static HTML/JS) for quick visibility.

## Getting Started (Backend)
```bash
python -m venv .venv
.\.venv\Scripts\activate  # or source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```
- Optional: copy `.env.example` to `.env` and add LLM settings.
  - Use your own model: set `LLM_PROVIDER=local` to run the built-in deterministic scorer (no external calls).
  - Or point to any OpenAI-compatible endpoint via `LLM_BASE_URL` + `LLM_MODEL`; use a dummy key if your server ignores it.
- Health check: `GET http://localhost:8000/health`.

## Dashboard
The dashboard is static (`frontend/index.html`). Serve it locally:
```bash
cd frontend
npm install
npm run dev   # uses serve on port 4173
```
Update `http://localhost:8000` in the fetch call if your API host/port differs. Deploy static assets to Vercel/Netlify/S3 as-is.

## Configs & Modes
- Global: `config/global.json` (`mode: "global"`, adminLevel: country/state).
- Ontario: `config/ontario.json` (`mode: "regional"`, adminLevel: city).
Switch by passing `{"mode":"global","configName":"global"}` or `{"mode":"regional","configName":"ontario"}` to endpoints.

## Forecasting
- Set `USE_TIMESERIES_FORECAST=1` to enable time-series risk estimation. Prophet is used if available (Python <3.13), otherwise a linear trend forecaster runs. Predictions include the flag `timeseries_enabled`.
- Backtesting helpers live in `grais/forecasting.py`. Smoke test: `USE_TIMESERIES_FORECAST=1 python scripts/smoke_forecast.py`

## API
Base URL: `http://localhost:8000`

- `POST /predict` → shortage predictions  
  Body: `{"mode":"regional","configName":"ontario"}` (optional `regions`, `horizonDays`)
- `POST /optimize` → allocation plan  
  Body: `{"mode":"global","configName":"global"}` (or provide `supplies`, `demands`, `distances`)
- `POST /prioritize` → prioritization with explanations  
  Body: `{"predictions":[...], "optimization": {...}, "configName":"ontario"}`
- `POST /recommend` → full pipeline (load config → predict → optimize → prioritize)  
  Body: `{"mode":"regional","configName":"ontario","scenario":{"depotOutages":["Depot-1"],"demandSurgeMultiplier":1.2,"budgetLimit":50000}}`
- `POST /recommend/compare` → run baseline plus a list of scenarios and return side-by-side results  
  Body: `{"mode":"regional","configName":"ontario","scenarios":[{"depotOutages":["Depot-1"]},{"demandSurgeMultiplier":1.3},{"budgetLimit":40000}]}`
- `GET /metrics` → in-memory metrics (requests, latency percentiles, solver status counts, unmet demand total)

### Scenarios (multi-resource ready)
- `depotOutages`: remove depots from supply/distance matrices.
- `demandSurgeMultiplier`: scales all regional demands.
- `budgetLimit`: caps transport cost in the optimizer.
- `distanceCapKm`: overrides `maxDistanceKm`.

## Smoke tests
- Multi-resource scenario: `python scripts/smoke_multi_resource.py`
- Observability QA path: `python scripts/smoke_observability.py`
- Scenario comparison: `python scripts/smoke_scenarios.py`
- Forecasting path: `USE_TIMESERIES_FORECAST=1 python scripts/smoke_forecast.py`

## Compatibility note
- For Python 3.13+, wheels for scikit-learn/ortools may be unavailable; the code falls back to heuristic prediction and pulp-based optimization automatically. For full stack with scikit-learn + OR-Tools, use Python 3.10–3.12.

## Deployment
- Backend: deploy `main.py` to Render/Railway/AWS; set `PORT` and env vars. Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
- Frontend: deploy `frontend/` as static site (Vercel/Netlify/S3/CloudFront). Update API base URL if using a hosted backend.

## Repository Layout
- `main.py` – FastAPI app & endpoints
- `grais/` – core modules (`prediction.py`, `optimization.py`, `prioritization.py`, `data_generator.py`, `config_loader.py`, `forecasting.py`)
- `config/` – mode configs (`global.json`, `ontario.json`)
- `frontend/` – static dashboard + package.json
- `docs/` – architecture and future work notes
- World sample config: config/world.json (mode: global)
- Remote data endpoints exposed locally: GET /data/regions/<config> and /data/depots/<config> (e.g., world_remote, ontario_real). Set dataSource="remote" to use them.
