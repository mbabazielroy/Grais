from __future__ import annotations

import logging
import os
import time
import json
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from grais.config_loader import ConfigNotFound, load_config, resolve_mode, CONFIG_DIR
from grais.data_generator import (
    Depot,
    RegionData,
    build_distance_matrix,
    generate_demands_from_predictions,
    generate_multi_resource_demands,
    generate_regions_from_config,
    generate_supply_depots,
)
from grais.optimization import optimize_allocation, multi_period_plan
from grais.prediction import predict_shortages
from grais.prioritization import prioritize_regions
from grais.metrics import metrics
from grais.data_loader import load_real_ontario_regions, load_real_ontario_depots
from grais.remote_loader import fetch_remote_regions, fetch_remote_depots
import uuid


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("grais")

app = FastAPI(title="GRAIS", version="0.1.0")
app.state.time_func = time.perf_counter
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def metrics_middleware(request, call_next):
    request_id = str(uuid.uuid4())
    start = app.state.time_func()
    response = None
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
    finally:
        duration_ms = (app.state.time_func() - start) * 1000
        endpoint = request.url.path
        metrics.record_request(endpoint, duration_ms)
        logger.info(
            "request",
            extra={
                "endpoint": endpoint,
                "method": request.method,
                "status": getattr(response, "status_code", None),
                "duration_ms": round(duration_ms, 2),
                "request_id": request_id,
            },
        )


class PredictRequest(BaseModel):
    regions: Optional[List[str]] = None
    mode: Optional[str] = None
    configName: str = "global"
    horizonDays: Optional[int] = None
    dataSource: Optional[str] = None


class OptimizeRequest(BaseModel):
    supplies: Optional[Dict[str, float]] = None
    demands: Optional[Dict[str, float]] = None
    distances: Optional[Dict[str, Dict[str, float]]] = None
    mode: Optional[str] = None
    configName: str = "global"
    scenario: Optional[Dict[str, Any]] = None
    dataSource: Optional[str] = None


class PrioritizeRequest(BaseModel):
    predictions: List[Dict]
    optimization: Dict
    mode: Optional[str] = None
    configName: str = "global"


class RecommendRequest(BaseModel):
    mode: Optional[str] = None
    configName: str = "global"
    scenario: Optional[Dict[str, Any]] = None
    dataSource: Optional[str] = None
    planningPeriods: Optional[int] = 1


class RecommendCompareRequest(BaseModel):
    mode: Optional[str] = None
    configName: str = "global"
    scenarios: List[Dict[str, Any]]


def _load_config(request_mode: Optional[str], config_name: str) -> Dict:
    cfg = load_config(config_name)
    mode = request_mode or cfg.get("mode")
    cfg["mode"] = resolve_mode(cfg if request_mode is None else {**cfg, "mode": request_mode})
    return cfg


def _to_regions(config: Dict, custom_regions: Optional[List[str]] = None) -> List[RegionData]:
    region_names = custom_regions or config.get("regions", [])
    return generate_regions_from_config({**config, "regions": region_names})
def _get_regions_and_depots(cfg: Dict, data_source: str = "synthetic", custom_regions: Optional[List[str]] = None):
    load_warnings: List[str] = []
    meta: Dict[str, Any] = {}
    if data_source in {"remote", "real"}:
        regions_url = cfg.get("remoteRegionsUrl")
        depots_url = cfg.get("remoteDepotsUrl")
        if regions_url and depots_url:
            try:
                regions, warn_r, meta_r = fetch_remote_regions(regions_url, cfg)
                depots, warn_d, meta_d = fetch_remote_depots(depots_url, cfg)
                load_warnings.extend(warn_r + warn_d)
                meta = {**meta_r, **meta_d}
                return regions, depots, load_warnings, "remote", meta
            except Exception as e:
                load_warnings.append(f"Failed to load remote data: {e}; falling back to synthetic.")
        if cfg.get("region", "").lower().startswith("ontario"):
            try:
                regions, warn_r = load_real_ontario_regions(cfg)
                depots, warn_d = load_real_ontario_depots(cfg)
                load_warnings.extend(warn_r + warn_d)
                return regions, depots, load_warnings, "real", meta
            except Exception as e:
                load_warnings.append(f"Failed to load real data: {e}; falling back to synthetic.")
    # Fallback to synthetic
    regions = _to_regions(cfg, custom_regions)
    depots = generate_supply_depots(cfg)
    return regions, depots, load_warnings, "synthetic", meta


def _resource_key(config: Dict) -> str:
    resources = config.get("defaultResourceTypes", [])
    return resources[0] if resources else "resource"


def apply_scenario(
    supplies: Dict,
    demands: Dict,
    distances: Dict[str, Dict[str, float]],
    config: Dict,
    scenario: Optional[Dict[str, Any]],
):
    if not scenario:
        return supplies, demands, distances, config

    cfg = dict(config)
    supplies_adj = dict(supplies)
    demands_adj = dict(demands)
    distances_adj = {k: dict(v) for k, v in distances.items()}

    outages = scenario.get("depotOutages") or []
    for outage in outages:
        supplies_adj.pop(outage, None)
        distances_adj.pop(outage, None)

    surge = scenario.get("demandSurgeMultiplier")
    if surge:
        if demands_adj and isinstance(next(iter(demands_adj.values())), dict):
            for res_map in demands_adj.values():
                for region in res_map:
                    res_map[region] *= surge
        else:
            for region in demands_adj:
                demands_adj[region] *= surge

    if scenario.get("distanceCapKm"):
        cfg["maxDistanceKm"] = scenario["distanceCapKm"]

    if scenario.get("budgetLimit"):
        cfg["budgetLimit"] = scenario["budgetLimit"]

    return supplies_adj, demands_adj, distances_adj, cfg


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def get_metrics() -> Dict[str, Any]:
    return metrics.snapshot()


@app.get("/admin/metrics")
def get_admin_metrics() -> Dict[str, Any]:
    return {"metrics": metrics.snapshot()}


@app.get("/data/regions/{config_name}")
def get_regions_data(config_name: str) -> Dict[str, Any]:
    # Try multiple naming conventions for flexibility
    candidates = [
        CONFIG_DIR / f"{config_name}_remote_regions.json",
        CONFIG_DIR / f"{config_name}_regions.json",
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="Regions data not found")


@app.get("/data/depots/{config_name}")
def get_depots_data(config_name: str) -> Dict[str, Any]:
    # Try multiple naming conventions for flexibility
    candidates = [
        CONFIG_DIR / f"{config_name}_remote_depots.json",
        CONFIG_DIR / f"{config_name}_depots.json",
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="Depots data not found")


@app.post("/predict")
def predict(req: PredictRequest) -> Dict[str, Any]:
    try:
        cfg = _load_config(req.mode, req.configName)
    except ConfigNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    regions, depots_unused, load_warnings, source_used, meta = _get_regions_and_depots(cfg, req.dataSource or "synthetic", req.regions)
    horizon = req.horizonDays or cfg.get("timeHorizonDays", 7)
    predictions = predict_shortages(regions, horizon, cfg["mode"], cfg)
    return {
        "predictions": predictions,
        "mode": cfg["mode"],
        "config": req.configName,
        "metadata": {**_metadata(source_used, load_warnings, meta), "warnings": load_warnings},
    }


@app.post("/optimize")
def optimize(req: OptimizeRequest) -> Dict[str, Any]:
    try:
        cfg = _load_config(req.mode, req.configName)
    except ConfigNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    mode = cfg["mode"]
    scenario = req.scenario
    if req.supplies and req.demands and req.distances:
        supplies, demands, distances, cfg_adj = apply_scenario(req.supplies, req.demands, req.distances, cfg, scenario)
        allocation = optimize_allocation(supplies, demands, distances, mode, cfg_adj)
        _record_allocation_metrics(allocation)
        return allocation

    # Generate synthetic scenario if not supplied
    regions, depots, load_warnings, source_used, meta = _get_regions_and_depots(cfg, req.dataSource or "synthetic")
    predictions = predict_shortages(regions, cfg.get("timeHorizonDays", 7), mode, cfg)
    supplies = {depot.name: depot.stock for depot in depots}
    distances = build_distance_matrix(depots, regions)
    demands = generate_multi_resource_demands(predictions, cfg)
    supplies, demands, distances, cfg_adj = apply_scenario(supplies, demands, distances, cfg, scenario)
    allocation = optimize_allocation(supplies, demands, distances, mode, cfg_adj)
    _record_allocation_metrics(allocation)
    return {
        "allocation": allocation,
        "inputs": {"supplies": supplies, "demands": demands, "distances": distances},
        "mode": mode,
        "scenario": scenario,
        "config": cfg_adj,
        "metadata": _metadata(source_used, load_warnings, meta),
        "warnings": load_warnings,
    }


@app.post("/prioritize")
def prioritize(req: PrioritizeRequest) -> Dict[str, Any]:
    try:
        cfg = _load_config(req.mode, req.configName)
    except ConfigNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    results = prioritize_regions(req.predictions, req.optimization, cfg["mode"], cfg)
    return {"priorities": results, "mode": cfg["mode"]}


@app.post("/recommend")
def recommend(req: RecommendRequest) -> Dict[str, Any]:
    try:
        cfg = _load_config(req.mode, req.configName)
    except ConfigNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))

    regions, depots, load_warnings, source_used, meta = _get_regions_and_depots(cfg, req.dataSource or "synthetic")
    predictions = predict_shortages(regions, cfg.get("timeHorizonDays", 7), cfg["mode"], cfg)
    supplies = {depot.name: depot.stock for depot in depots}
    distances = build_distance_matrix(depots, regions)
    demands = generate_multi_resource_demands(predictions, cfg)

    result = _run_pipeline(
        cfg,
        predictions,
        supplies,
        distances,
        demands,
        req.scenario,
        load_warnings,
        source_used,
        planning_periods=req.planningPeriods or 1,
        data_meta=meta,
    )
    return result


@app.post("/recommend/compare")
def recommend_compare(req: RecommendCompareRequest) -> Dict[str, Any]:
    try:
        cfg = _load_config(req.mode, req.configName)
    except ConfigNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))

    regions = _to_regions(cfg)
    predictions = predict_shortages(regions, cfg.get("timeHorizonDays", 7), cfg["mode"], cfg)
    depots = generate_supply_depots(cfg)
    supplies = {depot.name: depot.stock for depot in depots}
    distances = build_distance_matrix(depots, regions)
    demands = generate_multi_resource_demands(predictions, cfg)

    baseline = _run_pipeline(cfg, predictions, supplies, distances, demands, scenario=None, load_warnings=None, data_source="synthetic")
    scenarios_out = []
    for scenario in req.scenarios:
        scenarios_out.append(_run_pipeline(cfg, predictions, supplies, distances, demands, scenario))

    comparison = {
        "baseline": _summary(baseline),
        "scenarios": [{**_summary(s), "scenario": s.get("scenario")} for s in scenarios_out],
    }
    return {"baseline": baseline, "scenarios": scenarios_out, "comparison": comparison}


def _run_pipeline(
    cfg,
    predictions,
    supplies,
    distances,
    demands,
    scenario,
    load_warnings=None,
    data_source="synthetic",
    planning_periods: int = 1,
    data_meta: Optional[Dict[str, Any]] = None,
):
    supplies_adj, demands_adj, distances_adj, cfg_adj = apply_scenario(supplies, demands, distances, cfg, scenario)
    if planning_periods and planning_periods > 1:
        allocation = multi_period_plan(supplies_adj, demands_adj, distances_adj, cfg_adj["mode"], cfg_adj, planning_periods)
    else:
        allocation = optimize_allocation(supplies_adj, demands_adj, distances_adj, cfg_adj["mode"], cfg_adj)
    priorities = prioritize_regions(predictions, allocation, cfg_adj["mode"], cfg_adj)
    _record_allocation_metrics(allocation)
    return {
        "config": cfg_adj,
        "predictions": predictions,
        "allocation": allocation,
        "priorities": priorities,
        "scenario": scenario,
        "metadata": _metadata(data_source, load_warnings, data_meta or {}),
        "warnings": (_warnings(cfg_adj, allocation, scenario) + (load_warnings or [])),
    }


def _record_allocation_metrics(allocation: Dict) -> None:
    status = allocation.get("solverStatus", "UNKNOWN")
    metrics.record_solver_status(status)
    unmet_total = sum(item.get("unmet", 0) for item in allocation.get("unmet", []))
    metrics.record_unmet(unmet_total)


def _summary(result: Dict[str, Any]) -> Dict[str, Any]:
    alloc = result.get("allocation", {})
    unmet_total = sum(item.get("unmet", 0) for item in alloc.get("unmet", []))
    return {
        "objective": alloc.get("objective"),
        "unmetTotal": round(unmet_total, 2),
        "solverStatus": alloc.get("solverStatus"),
    }


def _metadata(data_source: str = "synthetic", warnings=None, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    timeseries = os.getenv("USE_TIMESERIES_FORECAST", "0") == "1"
    calibrated = True  # we use calibrated LR when sklearn is available
    from grais.optimization import pywraplp, pulp  # type: ignore
    from grais.forecasting import Prophet  # type: ignore

    # Check sklearn availability
    try:
        from sklearn.linear_model import LogisticRegression
        sklearn_available = True
    except ImportError:
        sklearn_available = False

    capabilities = {
        "sklearn": sklearn_available,
        "timeseries": timeseries,
        "prophet": Prophet is not None,
        "ortools": pywraplp is not None,
        "pulp": pulp is not None,
    }
    degraded_reasons = []
    if not capabilities["sklearn"]:
        degraded_reasons.append("sklearn missing; heuristic prediction")
    if not capabilities["ortools"]:
        degraded_reasons.append("ortools missing; using pulp fallback")
    if timeseries and not capabilities["prophet"]:
        degraded_reasons.append("Prophet missing; using linear trend timeseries")

    last_updated = None
    stale = False
    if meta:
        last_updated = meta.get("last_updated")
        ts = meta.get("timestamp")
        stale_secs = 3600  # 1h default
        try:
            import time

            stale = bool(ts and (time.time() - ts > stale_secs))
        except Exception:
            stale = False
        if stale:
            degraded_reasons.append("Remote data stale; using cached values")

    return {
        "forecasting_enabled": timeseries,
        "llm_provider": os.getenv("LLM_PROVIDER", "local"),
        "data_source": data_source,
        "data_source_detail": "Synthetic generator seeded from config; swap with real data loader for production"
        if data_source == "synthetic"
        else "Ontario sample data (replace with live open data)",
        "prediction_model": ("timeseries+" if timeseries else "") + ("calibrated" if calibrated else "baseline"),
        "load_warnings": warnings or [],
        "capabilities": capabilities,
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
        "last_updated": last_updated,
        "stale": stale,
    }


def _warnings(cfg: Dict[str, Any], allocation: Dict[str, Any], scenario: Optional[Dict[str, Any]]) -> List[str]:
    notes = []
    unmet_total = sum(item.get("unmet", 0) for item in allocation.get("unmet", []))
    if unmet_total > 0:
        notes.append(f"Unmet demand detected: {round(unmet_total, 2)} units across {len(allocation.get('unmet', []))} regions.")
    if cfg.get("budgetLimit"):
        notes.append(f"Budget cap active: {cfg['budgetLimit']}.")
    if cfg.get("maxDistanceKm"):
        notes.append(f"Distance cap active: {cfg['maxDistanceKm']} km.")
    if scenario and scenario.get("depotOutages"):
        notes.append(f"Depot outages: {', '.join(scenario.get('depotOutages'))}.")
    if cfg.get("modeStrictness") == "strict":
        notes.append("Strict mode: unmet heavily penalized.")
    return notes


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=False,
    )
