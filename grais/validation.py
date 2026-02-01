"""Input validation and schema definitions for GRAIS API."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field, field_validator, model_validator


# Constants for validation
VALID_MODES = {"global", "regional"}
VALID_ADMIN_LEVELS = {"country", "state", "province", "city", "district"}
VALID_DATA_SOURCES = {"synthetic", "real", "remote"}
VALID_RESOURCE_TYPES = {"food", "water", "medicine", "shelter", "fuel", "equipment"}
MAX_REGIONS = 1000
MAX_DEPOTS = 100
MAX_HORIZON_DAYS = 365
MAX_PLANNING_PERIODS = 52
MAX_CONFIG_NAME_LENGTH = 64
MAX_REGION_NAME_LENGTH = 128

# Regex for safe identifiers
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\s\.]+$")


class ValidationError(Exception):
    """Custom validation error with structured details."""

    def __init__(self, message: str, field: Optional[str] = None, details: Optional[Dict] = None):
        self.message = message
        self.field = field
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": "validation_error",
            "message": self.message,
            "field": self.field,
            "details": self.details,
        }


class ConfigSchema(BaseModel):
    """Schema for configuration files."""

    mode: str = Field(default="global", description="Operating mode: global or regional")
    region: str = Field(default="World", description="Region name for context")
    adminLevel: str = Field(default="country", description="Administrative level")
    timeHorizonDays: int = Field(default=7, ge=1, le=MAX_HORIZON_DAYS)
    maxDistanceKm: Optional[float] = Field(default=None, ge=0)
    budgetLimit: Optional[float] = Field(default=None, ge=0)
    vehicleCapacityPerTrip: Optional[float] = Field(default=None, ge=0)
    tripsPerDepot: Optional[int] = Field(default=None, ge=0)
    modeStrictness: str = Field(default="relaxed")
    defaultResourceTypes: List[str] = Field(default_factory=lambda: ["food", "water", "medicine", "shelter"])
    regions: List[str] = Field(default_factory=list)
    depots: List[str] = Field(default_factory=list)
    baseCoordinates: Optional[Dict[str, float]] = None
    coordinateSpread: Optional[float] = None
    remoteRegionsUrl: Optional[str] = None
    remoteDepotsUrl: Optional[str] = None
    dataSource: Optional[str] = None
    coldChainResources: List[str] = Field(default_factory=list)
    coldChainEnabled: bool = Field(default=False)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v.lower() not in VALID_MODES:
            raise ValueError(f"Invalid mode '{v}'. Must be one of: {VALID_MODES}")
        return v.lower()

    @field_validator("adminLevel")
    @classmethod
    def validate_admin_level(cls, v: str) -> str:
        if v.lower() not in VALID_ADMIN_LEVELS:
            raise ValueError(f"Invalid adminLevel '{v}'. Must be one of: {VALID_ADMIN_LEVELS}")
        return v.lower()

    @field_validator("modeStrictness")
    @classmethod
    def validate_strictness(cls, v: str) -> str:
        if v.lower() not in {"relaxed", "strict"}:
            raise ValueError("modeStrictness must be 'relaxed' or 'strict'")
        return v.lower()

    @field_validator("regions")
    @classmethod
    def validate_regions_list(cls, v: List[str]) -> List[str]:
        if len(v) > MAX_REGIONS:
            raise ValueError(f"Too many regions. Maximum allowed: {MAX_REGIONS}")
        for region in v:
            if not SAFE_IDENTIFIER_PATTERN.match(region):
                raise ValueError(f"Invalid region name '{region}'. Use only alphanumeric characters, spaces, hyphens, underscores, and dots.")
            if len(region) > MAX_REGION_NAME_LENGTH:
                raise ValueError(f"Region name '{region}' too long. Maximum: {MAX_REGION_NAME_LENGTH} characters")
        return v

    @field_validator("defaultResourceTypes")
    @classmethod
    def validate_resource_types(cls, v: List[str]) -> List[str]:
        invalid = set(v) - VALID_RESOURCE_TYPES
        if invalid:
            raise ValueError(f"Invalid resource types: {invalid}. Valid types: {VALID_RESOURCE_TYPES}")
        return v


class ScenarioSchema(BaseModel):
    """Schema for scenario parameters."""

    depotOutages: Optional[List[str]] = Field(default=None, description="List of depot names to simulate as unavailable")
    demandSurgeMultiplier: Optional[float] = Field(default=None, ge=0.1, le=10.0, description="Multiplier for demand surge")
    budgetLimit: Optional[float] = Field(default=None, ge=0, description="Budget cap for transport costs")
    distanceCapKm: Optional[float] = Field(default=None, ge=0, description="Maximum distance cap in km")

    @field_validator("depotOutages")
    @classmethod
    def validate_outages(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        if len(v) > MAX_DEPOTS:
            raise ValueError(f"Too many depot outages. Maximum: {MAX_DEPOTS}")
        for depot in v:
            if not SAFE_IDENTIFIER_PATTERN.match(depot):
                raise ValueError(f"Invalid depot name '{depot}'")
        return v


def validate_config_name(name: str) -> str:
    """Validate and sanitize config name."""
    if not name:
        raise ValidationError("Config name cannot be empty", field="configName")
    if len(name) > MAX_CONFIG_NAME_LENGTH:
        raise ValidationError(
            f"Config name too long. Maximum: {MAX_CONFIG_NAME_LENGTH} characters",
            field="configName",
        )
    if not SAFE_IDENTIFIER_PATTERN.match(name):
        raise ValidationError(
            "Config name contains invalid characters. Use only alphanumeric, hyphens, and underscores.",
            field="configName",
        )
    # Prevent path traversal
    if ".." in name or "/" in name or "\\" in name:
        raise ValidationError("Config name contains invalid path characters", field="configName")
    return name


def validate_region_names(regions: Optional[List[str]]) -> Optional[List[str]]:
    """Validate a list of region names."""
    if regions is None:
        return None
    if len(regions) > MAX_REGIONS:
        raise ValidationError(f"Too many regions. Maximum: {MAX_REGIONS}", field="regions")
    validated = []
    for region in regions:
        if not SAFE_IDENTIFIER_PATTERN.match(region):
            raise ValidationError(
                f"Invalid region name '{region}'",
                field="regions",
                details={"invalid_region": region},
            )
        if len(region) > MAX_REGION_NAME_LENGTH:
            raise ValidationError(
                f"Region name too long: '{region}'",
                field="regions",
            )
        validated.append(region)
    return validated


def validate_supplies(supplies: Dict[str, Any]) -> Dict[str, Any]:
    """Validate supply dictionary structure."""
    if not supplies:
        raise ValidationError("Supplies cannot be empty", field="supplies")
    if len(supplies) > MAX_DEPOTS:
        raise ValidationError(f"Too many depots. Maximum: {MAX_DEPOTS}", field="supplies")
    for depot, stock in supplies.items():
        if not SAFE_IDENTIFIER_PATTERN.match(depot):
            raise ValidationError(f"Invalid depot name '{depot}'", field="supplies")
        if isinstance(stock, dict):
            for resource, qty in stock.items():
                if not isinstance(qty, (int, float)) or qty < 0:
                    raise ValidationError(
                        f"Invalid quantity for {depot}/{resource}",
                        field="supplies",
                    )
        elif not isinstance(stock, (int, float)) or stock < 0:
            raise ValidationError(f"Invalid stock for depot '{depot}'", field="supplies")
    return supplies


def validate_demands(demands: Dict[str, Any]) -> Dict[str, Any]:
    """Validate demand dictionary structure."""
    if not demands:
        raise ValidationError("Demands cannot be empty", field="demands")
    for key, value in demands.items():
        if isinstance(value, dict):
            # Multi-resource format: resource -> {region -> qty}
            for region, qty in value.items():
                if not isinstance(qty, (int, float)) or qty < 0:
                    raise ValidationError(
                        f"Invalid demand quantity for {key}/{region}",
                        field="demands",
                    )
        elif not isinstance(value, (int, float)) or value < 0:
            # Single-resource format: region -> qty
            raise ValidationError(f"Invalid demand for '{key}'", field="demands")
    return demands


def validate_distances(distances: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    """Validate distance matrix structure."""
    if not distances:
        raise ValidationError("Distances cannot be empty", field="distances")
    for depot, regions in distances.items():
        if not SAFE_IDENTIFIER_PATTERN.match(depot):
            raise ValidationError(f"Invalid depot name '{depot}'", field="distances")
        if not isinstance(regions, dict):
            raise ValidationError(f"Invalid distance data for depot '{depot}'", field="distances")
        for region, dist in regions.items():
            if not isinstance(dist, (int, float)) or dist < 0:
                raise ValidationError(
                    f"Invalid distance value for {depot} -> {region}",
                    field="distances",
                )
    return distances


def validate_predictions(predictions: List[Dict]) -> List[Dict]:
    """Validate prediction list structure."""
    if not predictions:
        raise ValidationError("Predictions cannot be empty", field="predictions")
    required_keys = {"region", "shortageProbability", "shortageSeverity", "expectedShortageDay"}
    for i, pred in enumerate(predictions):
        missing = required_keys - set(pred.keys())
        if missing:
            raise ValidationError(
                f"Prediction {i} missing required fields: {missing}",
                field="predictions",
                details={"index": i, "missing_fields": list(missing)},
            )
        prob = pred.get("shortageProbability")
        if not isinstance(prob, (int, float)) or not (0 <= prob <= 1):
            raise ValidationError(
                f"Invalid shortageProbability in prediction {i}",
                field="predictions",
            )
    return predictions


def validate_optimization_result(optimization: Dict) -> Dict:
    """Validate optimization result structure."""
    if not optimization:
        raise ValidationError("Optimization result cannot be empty", field="optimization")
    # Should have at least shipments key
    if "shipments" not in optimization and "unmet" not in optimization:
        raise ValidationError(
            "Optimization result must contain 'shipments' or 'unmet'",
            field="optimization",
        )
    return optimization


def sanitize_string(value: str, max_length: int = 256) -> str:
    """Sanitize a string input."""
    if not isinstance(value, str):
        raise ValidationError("Expected string value")
    # Remove null bytes and control characters
    sanitized = "".join(c for c in value if c.isprintable() or c in " \t\n")
    return sanitized[:max_length]
