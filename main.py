"""
GRAIS - Global Resource Allocation Intelligence System

Production-ready API for predicting shortages, optimizing allocations,
and prioritizing humanitarian response across global and regional contexts.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

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
from grais.middleware import (
    RequestContextMiddleware,
    RateLimitMiddleware,
    setup_logging,
    get_cors_origins,
)
from grais.errors import (
    GRAISError,
    ConfigNotFoundError,
    ValidationError as GRAISValidationError,
    OptimizationError,
    grais_exception_handler,
    http_exception_handler,
    generic_exception_handler,
    create_error_response,
)
from grais.validation import (
    validate_config_name,
    validate_region_names,
    validate_supplies,
    validate_demands,
    validate_distances,
    validate_predictions,
    validate_optimization_result,
    ValidationError,
    VALID_MODES,
    VALID_DATA_SOURCES,
    MAX_HORIZON_DAYS,
    MAX_PLANNING_PERIODS,
)

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_JSON = os.getenv("LOG_FORMAT", "text").lower() == "json"
setup_logging(level=LOG_LEVEL, json_format=LOG_JSON)
logger = logging.getLogger("grais")

# Application metadata
APP_VERSION = "0.2.0"
APP_TITLE = "GRAIS - Global Resource Allocation Intelligence System"
APP_DESCRIPTION = """
Production-ready API for predicting resource shortages, optimizing supply allocations,
and prioritizing humanitarian response across global and regional contexts.

## Features
- **Prediction**: ML-based shortage probability estimation with optional time-series forecasting
- **Optimization**: Multi-resource linear programming for supply allocation
- **Prioritization**: Region ranking with LLM or heuristic explanations
- **Scenarios**: What-if analysis for depot outages, demand surges, and budget constraints

## Modes
- `global`: Country/state level allocation
- `regional`: City/district level allocation (e.g., Ontario)
"""

# Create FastAPI application with OpenAPI documentation
app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "health", "description": "Health and status endpoints"},
        {"name": "prediction", "description": "Shortage prediction endpoints"},
        {"name": "optimization", "description": "Resource allocation optimization"},
        {"name": "prioritization", "description": "Region prioritization"},
        {"name": "pipeline", "description": "Full recommendation pipeline"},
        {"name": "data", "description": "Data access endpoints"},
        {"name": "metrics", "description": "Observability and metrics"},
    ],
)

# Store time function for testing
app.state.time_func = time.perf_counter

# Add exception handlers
app.add_exception_handler(GRAISError, grais_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

# Add middleware (order matters - first added = outermost)
# Rate limiting
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "120"))
app.add_middleware(
    RateLimitMiddleware,
    requests_per_minute=RATE_LIMIT_RPM,
    enabled=RATE_LIMIT_ENABLED,
)

# Request context (ID, timing)
app.add_middleware(RequestContextMiddleware, time_func=app.state.time_func)

# CORS configuration
CORS_ORIGINS = get_cors_origins()
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS if CORS_ORIGINS else ["*"] if ENVIRONMENT != "production" else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
)


# ==============================================================================
# Request/Response Models with validation
# ==============================================================================

class PredictRequest(BaseModel):
    """Request body for shortage prediction."""
    regions: Optional[List[str]] = Field(
        default=None,
        description="List of specific region names to predict for. If not provided, uses config default.",
        max_length=1000,
    )
    mode: Optional[str] = Field(
        default=None,
        description="Operating mode: 'global' or 'regional'. Defaults to config setting.",
    )
    configName: str = Field(
        default="global",
        description="Name of the configuration file to use.",
        max_length=64,
    )
    horizonDays: Optional[int] = Field(
        default=None,
        ge=1,
        le=MAX_HORIZON_DAYS,
        description="Forecast horizon in days. Defaults to config setting.",
    )
    dataSource: Optional[str] = Field(
        default=None,
        description="Data source: 'synthetic', 'real', or 'remote'.",
    )

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v.lower() not in VALID_MODES:
            raise ValueError(f"Invalid mode. Must be one of: {VALID_MODES}")
        return v.lower() if v else v

    @field_validator("dataSource")
    @classmethod
    def validate_data_source(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v.lower() not in VALID_DATA_SOURCES:
            raise ValueError(f"Invalid dataSource. Must be one of: {VALID_DATA_SOURCES}")
        return v.lower() if v else v


class ScenarioParams(BaseModel):
    """Scenario parameters for what-if analysis."""
    depotOutages: Optional[List[str]] = Field(
        default=None,
        description="List of depot names to simulate as unavailable.",
        max_length=100,
    )
    demandSurgeMultiplier: Optional[float] = Field(
        default=None,
        ge=0.1,
        le=10.0,
        description="Multiplier for demand surge (e.g., 1.5 = 50% increase).",
    )
    budgetLimit: Optional[float] = Field(
        default=None,
        ge=0,
        description="Maximum transport budget cap.",
    )
    distanceCapKm: Optional[float] = Field(
        default=None,
        ge=0,
        description="Maximum distance cap in kilometers.",
    )


class OptimizeRequest(BaseModel):
    """Request body for allocation optimization."""
    supplies: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Supply per depot. Can be {depot: qty} or {depot: {resource: qty}}.",
    )
    demands: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Demand per region. Can be {region: qty} or {resource: {region: qty}}.",
    )
    distances: Optional[Dict[str, Dict[str, float]]] = Field(
        default=None,
        description="Distance matrix: {depot: {region: km}}.",
    )
    mode: Optional[str] = Field(default=None, description="Operating mode.")
    configName: str = Field(default="global", max_length=64)
    scenario: Optional[ScenarioParams] = Field(
        default=None,
        description="Scenario parameters for what-if analysis.",
    )
    dataSource: Optional[str] = Field(default=None)


class PrioritizeRequest(BaseModel):
    """Request body for region prioritization."""
    predictions: List[Dict] = Field(
        ...,
        description="List of prediction results from /predict endpoint.",
        min_length=1,
    )
    optimization: Dict = Field(
        ...,
        description="Optimization result from /optimize endpoint.",
    )
    mode: Optional[str] = Field(default=None)
    configName: str = Field(default="global", max_length=64)


class RecommendRequest(BaseModel):
    """Request body for full recommendation pipeline."""
    mode: Optional[str] = Field(default=None, description="Operating mode.")
    configName: str = Field(default="global", max_length=64)
    scenario: Optional[ScenarioParams] = Field(default=None)
    dataSource: Optional[str] = Field(default=None)
    planningPeriods: Optional[int] = Field(
        default=1,
        ge=1,
        le=MAX_PLANNING_PERIODS,
        description="Number of planning periods for multi-period optimization.",
    )


class RecommendCompareRequest(BaseModel):
    """Request body for scenario comparison."""
    mode: Optional[str] = Field(default=None)
    configName: str = Field(default="global", max_length=64)
    scenarios: List[ScenarioParams] = Field(
        ...,
        description="List of scenarios to compare against baseline.",
        min_length=1,
        max_length=10,
    )


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = Field(description="Service status: 'ok' or 'degraded'")
    version: str = Field(description="API version")
    environment: str = Field(description="Deployment environment")
    checks: Dict[str, bool] = Field(description="Individual component health checks")


class ErrorResponse(BaseModel):
    """Standard error response."""
    error: str = Field(description="Error code")
    message: str = Field(description="Human-readable error message")
    details: Dict[str, Any] = Field(default_factory=dict, description="Additional error details")
    request_id: Optional[str] = Field(default=None, description="Request ID for tracking")


# ==============================================================================
# Helper functions
# ==============================================================================

def _load_config(request_mode: Optional[str], config_name: str) -> Dict:
    """Load and validate configuration."""
    try:
        validated_name = validate_config_name(config_name)
    except ValidationError as e:
        raise GRAISValidationError(e.message, field=e.field)

    try:
        cfg = load_config(validated_name)
    except ConfigNotFound as e:
        raise ConfigNotFoundError(validated_name)

    mode = request_mode or cfg.get("mode")
    cfg["mode"] = resolve_mode(cfg if request_mode is None else {**cfg, "mode": request_mode})
    return cfg


def _to_regions(config: Dict, custom_regions: Optional[List[str]] = None) -> List[RegionData]:
    """Convert config to region data objects."""
    region_names = custom_regions or config.get("regions", [])
    return generate_regions_from_config({**config, "regions": region_names})


def _get_regions_and_depots(
    cfg: Dict,
    data_source: str = "synthetic",
    custom_regions: Optional[List[str]] = None,
):
    """Load regions and depots from appropriate data source."""
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
                logger.warning(f"Failed to load remote data: {e}")
                load_warnings.append(f"Failed to load remote data: {e}; falling back to synthetic.")

        if cfg.get("region", "").lower().startswith("ontario"):
            try:
                regions, warn_r = load_real_ontario_regions(cfg)
                depots, warn_d = load_real_ontario_depots(cfg)
                load_warnings.extend(warn_r + warn_d)
                return regions, depots, load_warnings, "real", meta
            except Exception as e:
                logger.warning(f"Failed to load real data: {e}")
                load_warnings.append(f"Failed to load real data: {e}; falling back to synthetic.")

    # Fallback to synthetic
    regions = _to_regions(cfg, custom_regions)
    depots = generate_supply_depots(cfg)
    return regions, depots, load_warnings, "synthetic", meta


def _resource_key(config: Dict) -> str:
    """Get the primary resource type from config."""
    resources = config.get("defaultResourceTypes", [])
    return resources[0] if resources else "resource"


def apply_scenario(
    supplies: Dict,
    demands: Dict,
    distances: Dict[str, Dict[str, float]],
    config: Dict,
    scenario: Optional[Union[Dict[str, Any], ScenarioParams]],
):
    """Apply scenario modifications to supplies, demands, and config."""
    if not scenario:
        return supplies, demands, distances, config

    # Handle both dict and ScenarioParams
    if isinstance(scenario, ScenarioParams):
        scenario = scenario.model_dump(exclude_none=True)

    cfg = dict(config)
    supplies_adj = dict(supplies)
    demands_adj = dict(demands)
    distances_adj = {k: dict(v) for k, v in distances.items()}

    # Apply depot outages
    outages = scenario.get("depotOutages") or []
    for outage in outages:
        supplies_adj.pop(outage, None)
        distances_adj.pop(outage, None)

    # Apply demand surge
    surge = scenario.get("demandSurgeMultiplier")
    if surge:
        if demands_adj and isinstance(next(iter(demands_adj.values())), dict):
            for res_map in demands_adj.values():
                for region in res_map:
                    res_map[region] *= surge
        else:
            for region in demands_adj:
                demands_adj[region] *= surge

    # Apply distance cap
    if scenario.get("distanceCapKm"):
        cfg["maxDistanceKm"] = scenario["distanceCapKm"]

    # Apply budget limit
    if scenario.get("budgetLimit"):
        cfg["budgetLimit"] = scenario["budgetLimit"]

    return supplies_adj, demands_adj, distances_adj, cfg


def _run_pipeline(
    cfg: Dict,
    predictions: List[Dict],
    supplies: Dict,
    distances: Dict,
    demands: Dict,
    scenario: Optional[Union[Dict, ScenarioParams]],
    load_warnings: Optional[List[str]] = None,
    data_source: str = "synthetic",
    planning_periods: int = 1,
    data_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the full prediction-optimization-prioritization pipeline."""
    supplies_adj, demands_adj, distances_adj, cfg_adj = apply_scenario(
        supplies, demands, distances, cfg, scenario
    )

    try:
        if planning_periods and planning_periods > 1:
            allocation = multi_period_plan(
                supplies_adj, demands_adj, distances_adj, cfg_adj["mode"], cfg_adj, planning_periods
            )
        else:
            allocation = optimize_allocation(
                supplies_adj, demands_adj, distances_adj, cfg_adj["mode"], cfg_adj
            )
    except RuntimeError as e:
        raise OptimizationError(str(e))

    priorities = prioritize_regions(predictions, allocation, cfg_adj["mode"], cfg_adj)
    _record_allocation_metrics(allocation)

    return {
        "config": cfg_adj,
        "predictions": predictions,
        "allocation": allocation,
        "priorities": priorities,
        "scenario": scenario.model_dump(exclude_none=True) if isinstance(scenario, ScenarioParams) else scenario,
        "metadata": _metadata(data_source, load_warnings, data_meta or {}),
        "warnings": (_warnings(cfg_adj, allocation, scenario) + (load_warnings or [])),
    }


def _record_allocation_metrics(allocation: Dict) -> None:
    """Record allocation metrics for observability."""
    status = allocation.get("solverStatus", "UNKNOWN")
    metrics.record_solver_status(status)
    unmet_total = sum(item.get("unmet", 0) for item in allocation.get("unmet", []))
    metrics.record_unmet(unmet_total)


def _summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """Extract summary statistics from pipeline result."""
    alloc = result.get("allocation", {})
    unmet_total = sum(item.get("unmet", 0) for item in alloc.get("unmet", []))
    return {
        "objective": alloc.get("objective"),
        "unmetTotal": round(unmet_total, 2),
        "solverStatus": alloc.get("solverStatus"),
    }


def _metadata(
    data_source: str = "synthetic",
    warnings: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build metadata about the pipeline execution."""
    timeseries = os.getenv("USE_TIMESERIES_FORECAST", "0") == "1"
    calibrated = True

    from grais.optimization import pywraplp, pulp
    from grais.forecasting import Prophet

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
            stale = bool(ts and (time.time() - ts > stale_secs))
        except Exception:
            stale = False
        if stale:
            degraded_reasons.append("Remote data stale; using cached values")

    return {
        "forecasting_enabled": timeseries,
        "llm_provider": os.getenv("LLM_PROVIDER", "local"),
        "data_source": data_source,
        "data_source_detail": (
            "Synthetic generator seeded from config; swap with real data loader for production"
            if data_source == "synthetic"
            else "Ontario sample data (replace with live open data)"
        ),
        "prediction_model": ("timeseries+" if timeseries else "") + ("calibrated" if calibrated else "baseline"),
        "load_warnings": warnings or [],
        "capabilities": capabilities,
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
        "last_updated": last_updated,
        "stale": stale,
    }


def _warnings(
    cfg: Dict[str, Any],
    allocation: Dict[str, Any],
    scenario: Optional[Union[Dict, ScenarioParams]],
) -> List[str]:
    """Generate warning messages based on results."""
    notes = []
    unmet_total = sum(item.get("unmet", 0) for item in allocation.get("unmet", []))

    if unmet_total > 0:
        notes.append(
            f"Unmet demand detected: {round(unmet_total, 2)} units across "
            f"{len(allocation.get('unmet', []))} regions."
        )

    if cfg.get("budgetLimit"):
        notes.append(f"Budget cap active: {cfg['budgetLimit']}.")

    if cfg.get("maxDistanceKm"):
        notes.append(f"Distance cap active: {cfg['maxDistanceKm']} km.")

    # Handle both dict and ScenarioParams
    if scenario:
        scenario_dict = scenario.model_dump(exclude_none=True) if isinstance(scenario, ScenarioParams) else scenario
        if scenario_dict.get("depotOutages"):
            notes.append(f"Depot outages: {', '.join(scenario_dict.get('depotOutages'))}.")

    if cfg.get("modeStrictness") == "strict":
        notes.append("Strict mode: unmet heavily penalized.")

    return notes


# ==============================================================================
# Health & Metrics Endpoints
# ==============================================================================

@app.get(
    "/health",
    tags=["health"],
    response_model=HealthResponse,
    summary="Health check",
    description="Check service health and component status.",
)
def health() -> Dict[str, Any]:
    """Comprehensive health check endpoint."""
    checks = {}

    # Check config loading
    try:
        load_config("global")
        checks["config"] = True
    except Exception:
        checks["config"] = False

    # Check sklearn availability
    try:
        from sklearn.linear_model import LogisticRegression
        checks["sklearn"] = True
    except ImportError:
        checks["sklearn"] = False

    # Check optimization solver
    try:
        from grais.optimization import pywraplp, pulp
        checks["solver"] = pywraplp is not None or pulp is not None
    except Exception:
        checks["solver"] = False

    # Determine overall status
    critical_checks = ["config", "solver"]
    all_critical_ok = all(checks.get(c, False) for c in critical_checks)
    status = "ok" if all_critical_ok else "degraded"

    return {
        "status": status,
        "version": APP_VERSION,
        "environment": ENVIRONMENT,
        "checks": checks,
    }


@app.get(
    "/health/ready",
    tags=["health"],
    summary="Readiness probe",
    description="Kubernetes readiness probe - returns 200 if service is ready to accept traffic.",
)
def health_ready() -> Dict[str, str]:
    """Readiness probe for Kubernetes."""
    return {"status": "ready"}


@app.get(
    "/health/live",
    tags=["health"],
    summary="Liveness probe",
    description="Kubernetes liveness probe - returns 200 if service is alive.",
)
def health_live() -> Dict[str, str]:
    """Liveness probe for Kubernetes."""
    return {"status": "alive"}


@app.get(
    "/metrics",
    tags=["metrics"],
    summary="Get application metrics",
    description="Returns request counts, latency percentiles, solver status distribution, and unmet demand totals.",
)
def get_metrics() -> Dict[str, Any]:
    """Get application metrics snapshot."""
    return metrics.snapshot()


@app.get(
    "/admin/metrics",
    tags=["metrics"],
    summary="Admin metrics endpoint",
    description="Detailed metrics for administrative monitoring.",
)
def get_admin_metrics() -> Dict[str, Any]:
    """Admin metrics endpoint with additional context."""
    return {
        "metrics": metrics.snapshot(),
        "version": APP_VERSION,
        "environment": ENVIRONMENT,
    }


# ==============================================================================
# Data Endpoints
# ==============================================================================

@app.get(
    "/data/regions/{config_name}",
    tags=["data"],
    summary="Get region data",
    description="Retrieve region data for a specific configuration.",
    responses={404: {"model": ErrorResponse, "description": "Data not found"}},
)
def get_regions_data(config_name: str) -> Dict[str, Any]:
    """Get region data from configuration files."""
    try:
        validated_name = validate_config_name(config_name)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.message)

    candidates = [
        CONFIG_DIR / f"{validated_name}_remote_regions.json",
        CONFIG_DIR / f"{validated_name}_regions.json",
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    raise HTTPException(status_code=404, detail=f"Regions data not found for config '{config_name}'")


@app.get(
    "/data/depots/{config_name}",
    tags=["data"],
    summary="Get depot data",
    description="Retrieve depot/supply data for a specific configuration.",
    responses={404: {"model": ErrorResponse, "description": "Data not found"}},
)
def get_depots_data(config_name: str) -> Dict[str, Any]:
    """Get depot data from configuration files."""
    try:
        validated_name = validate_config_name(config_name)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.message)

    candidates = [
        CONFIG_DIR / f"{validated_name}_remote_depots.json",
        CONFIG_DIR / f"{validated_name}_depots.json",
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    raise HTTPException(status_code=404, detail=f"Depots data not found for config '{config_name}'")


# ==============================================================================
# Prediction Endpoints
# ==============================================================================

@app.post(
    "/predict",
    tags=["prediction"],
    summary="Predict shortages",
    description="""
    Predict shortage probability, severity, and expected timing for regions.

    Uses calibrated ML model with optional time-series forecasting (if enabled via USE_TIMESERIES_FORECAST=1).
    """,
    responses={
        404: {"model": ErrorResponse, "description": "Configuration not found"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
)
def predict(req: PredictRequest) -> Dict[str, Any]:
    """Generate shortage predictions for regions."""
    cfg = _load_config(req.mode, req.configName)

    regions, depots_unused, load_warnings, source_used, meta = _get_regions_and_depots(
        cfg, req.dataSource or "synthetic", req.regions
    )

    horizon = req.horizonDays or cfg.get("timeHorizonDays", 7)
    predictions = predict_shortages(regions, horizon, cfg["mode"], cfg)

    return {
        "predictions": predictions,
        "mode": cfg["mode"],
        "config": req.configName,
        "metadata": {**_metadata(source_used, load_warnings, meta), "warnings": load_warnings},
    }


# ==============================================================================
# Optimization Endpoints
# ==============================================================================

@app.post(
    "/optimize",
    tags=["optimization"],
    summary="Optimize resource allocation",
    description="""
    Solve multi-resource allocation using linear programming.

    Minimizes transport costs while meeting demand constraints, with penalties for unmet demand.
    Supports distance caps, budget limits, and vehicle capacity constraints.
    """,
    responses={
        404: {"model": ErrorResponse, "description": "Configuration not found"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Optimization failed"},
    },
)
def optimize(req: OptimizeRequest) -> Dict[str, Any]:
    """Optimize resource allocation from depots to regions."""
    cfg = _load_config(req.mode, req.configName)
    mode = cfg["mode"]
    scenario = req.scenario

    # Use provided data if available
    if req.supplies and req.demands and req.distances:
        try:
            validate_supplies(req.supplies)
            validate_demands(req.demands)
            validate_distances(req.distances)
        except ValidationError as e:
            raise GRAISValidationError(e.message, field=e.field)

        supplies, demands, distances, cfg_adj = apply_scenario(
            req.supplies, req.demands, req.distances, cfg, scenario
        )

        try:
            allocation = optimize_allocation(supplies, demands, distances, mode, cfg_adj)
        except RuntimeError as e:
            raise OptimizationError(str(e))

        _record_allocation_metrics(allocation)
        return allocation

    # Generate synthetic data if not provided
    regions, depots, load_warnings, source_used, meta = _get_regions_and_depots(
        cfg, req.dataSource or "synthetic"
    )
    predictions = predict_shortages(regions, cfg.get("timeHorizonDays", 7), mode, cfg)
    supplies = {depot.name: depot.stock for depot in depots}
    distances = build_distance_matrix(depots, regions)
    demands = generate_multi_resource_demands(predictions, cfg)

    supplies, demands, distances, cfg_adj = apply_scenario(supplies, demands, distances, cfg, scenario)

    try:
        allocation = optimize_allocation(supplies, demands, distances, mode, cfg_adj)
    except RuntimeError as e:
        raise OptimizationError(str(e))

    _record_allocation_metrics(allocation)

    return {
        "allocation": allocation,
        "inputs": {"supplies": supplies, "demands": demands, "distances": distances},
        "mode": mode,
        "scenario": scenario.model_dump(exclude_none=True) if scenario else None,
        "config": cfg_adj,
        "metadata": _metadata(source_used, load_warnings, meta),
        "warnings": load_warnings,
    }


# ==============================================================================
# Prioritization Endpoints
# ==============================================================================

@app.post(
    "/prioritize",
    tags=["prioritization"],
    summary="Prioritize regions",
    description="""
    Rank regions by urgency based on predictions and allocation results.

    Combines shortage probability, severity, urgency timing, and unmet demand into a priority score.
    Uses LLM for explanations if configured, otherwise uses heuristic scoring.
    """,
    responses={
        404: {"model": ErrorResponse, "description": "Configuration not found"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
)
def prioritize(req: PrioritizeRequest) -> Dict[str, Any]:
    """Prioritize regions based on predictions and optimization results."""
    cfg = _load_config(req.mode, req.configName)

    try:
        validate_predictions(req.predictions)
        validate_optimization_result(req.optimization)
    except ValidationError as e:
        raise GRAISValidationError(e.message, field=e.field)

    results = prioritize_regions(req.predictions, req.optimization, cfg["mode"], cfg)
    return {"priorities": results, "mode": cfg["mode"]}


# ==============================================================================
# Pipeline Endpoints
# ==============================================================================

@app.post(
    "/recommend",
    tags=["pipeline"],
    summary="Full recommendation pipeline",
    description="""
    Run the complete pipeline: predict → optimize → prioritize.

    This is the main endpoint for getting actionable recommendations.
    Supports scenario analysis and multi-period planning.
    """,
    responses={
        404: {"model": ErrorResponse, "description": "Configuration not found"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Pipeline failed"},
    },
)
def recommend(req: RecommendRequest) -> Dict[str, Any]:
    """Run full recommendation pipeline."""
    cfg = _load_config(req.mode, req.configName)

    regions, depots, load_warnings, source_used, meta = _get_regions_and_depots(
        cfg, req.dataSource or "synthetic"
    )
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


@app.post(
    "/recommend/compare",
    tags=["pipeline"],
    summary="Compare scenarios",
    description="""
    Compare multiple scenarios against baseline.

    Useful for what-if analysis: depot outages, demand surges, budget constraints.
    Returns side-by-side comparison of key metrics.
    """,
    responses={
        404: {"model": ErrorResponse, "description": "Configuration not found"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Comparison failed"},
    },
)
def recommend_compare(req: RecommendCompareRequest) -> Dict[str, Any]:
    """Compare multiple scenarios against baseline."""
    cfg = _load_config(req.mode, req.configName)

    regions = _to_regions(cfg)
    predictions = predict_shortages(regions, cfg.get("timeHorizonDays", 7), cfg["mode"], cfg)
    depots = generate_supply_depots(cfg)
    supplies = {depot.name: depot.stock for depot in depots}
    distances = build_distance_matrix(depots, regions)
    demands = generate_multi_resource_demands(predictions, cfg)

    baseline = _run_pipeline(
        cfg, predictions, supplies, distances, demands,
        scenario=None, load_warnings=None, data_source="synthetic"
    )

    scenarios_out = []
    for scenario in req.scenarios:
        scenarios_out.append(_run_pipeline(cfg, predictions, supplies, distances, demands, scenario))

    comparison = {
        "baseline": _summary(baseline),
        "scenarios": [{**_summary(s), "scenario": s.get("scenario")} for s in scenarios_out],
    }
    return {"baseline": baseline, "scenarios": scenarios_out, "comparison": comparison}


# ==============================================================================
# Application Entry Point
# ==============================================================================

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    reload = os.getenv("RELOAD", "false").lower() == "true"

    logger.info(f"Starting GRAIS API v{APP_VERSION} on {host}:{port}")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload,
    )
