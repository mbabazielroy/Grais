"""
Real Data Integration Module for GRAIS

This module provides utilities for integrating real-world data sources
into the GRAIS pipeline. It supports:
- CSV file imports
- JSON file imports
- REST API endpoints
- Database connections (with adapters)

Example Usage:
    # From CSV files
    from grais.real_data import load_regions_from_csv, load_depots_from_csv
    regions = load_regions_from_csv("path/to/regions.csv", config)
    depots = load_depots_from_csv("path/to/depots.csv", config)
    
    # From REST API
    from grais.real_data import RealDataLoader
    loader = RealDataLoader(config)
    regions = loader.fetch_regions_from_api("https://api.example.com/regions")
    depots = loader.fetch_depots_from_api("https://api.example.com/depots")
    
    # From database
    regions = loader.fetch_regions_from_db(connection_string, query)
"""
from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False
    pd = None

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from grais.data_generator import RegionData, Depot

logger = logging.getLogger("grais.real_data")


# =============================================================================
# Data Validation
# =============================================================================

@dataclass
class ValidationResult:
    """Result of data validation."""
    valid: bool
    errors: List[str]
    warnings: List[str]
    records_processed: int
    records_valid: int


def validate_region_data(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a single region data record."""
    errors = []
    
    # Required fields
    if not data.get("name"):
        errors.append("Missing required field: name")
    
    # Population should be positive
    population = data.get("population", 0)
    if not isinstance(population, (int, float)) or population < 0:
        errors.append(f"Invalid population: {population}")
    
    # Coordinates
    lat = data.get("lat") or data.get("latitude")
    lon = data.get("lon") or data.get("longitude") or data.get("lng")
    if lat is None or lon is None:
        errors.append("Missing coordinates (lat/lon)")
    elif not (-90 <= float(lat) <= 90) or not (-180 <= float(lon) <= 180):
        errors.append(f"Invalid coordinates: ({lat}, {lon})")
    
    return len(errors) == 0, errors


def validate_depot_data(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a single depot data record."""
    errors = []
    
    if not data.get("name"):
        errors.append("Missing required field: name")
    
    lat = data.get("lat") or data.get("latitude")
    lon = data.get("lon") or data.get("longitude") or data.get("lng")
    if lat is None or lon is None:
        errors.append("Missing coordinates (lat/lon)")
    
    # Stock validation
    stock = data.get("stock", {})
    if isinstance(stock, dict):
        for resource, qty in stock.items():
            if not isinstance(qty, (int, float)) or qty < 0:
                errors.append(f"Invalid stock for {resource}: {qty}")
    
    return len(errors) == 0, errors


# =============================================================================
# CSV Import Functions
# =============================================================================

def load_regions_from_csv(
    filepath: Union[str, Path],
    config: Dict[str, Any],
    column_mapping: Optional[Dict[str, str]] = None,
) -> Tuple[List[RegionData], ValidationResult]:
    """
    Load region data from a CSV file.
    
    Expected CSV columns (or mapped equivalents):
        - name: Region name (required)
        - population: Population count (required)
        - lat/latitude: Latitude coordinate (required)
        - lon/longitude/lng: Longitude coordinate (required)
        - risk_factor: Risk factor 0-1 (optional, default 0.2)
        - supply_food, supply_water, etc.: Current supply per resource (optional)
        - consumption_food, consumption_water, etc.: Consumption rate per resource (optional)
    
    Args:
        filepath: Path to CSV file
        config: GRAIS configuration dict
        column_mapping: Optional mapping of CSV columns to expected names
            e.g., {"region_name": "name", "pop": "population"}
    
    Returns:
        Tuple of (list of RegionData, ValidationResult)
    
    Example CSV:
        name,population,lat,lon,risk_factor,supply_food,supply_water
        Toronto,2930000,43.65,-79.38,0.3,50000,80000
        Ottawa,1017449,45.42,-75.69,0.2,30000,45000
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"CSV file not found: {filepath}")
    
    column_mapping = column_mapping or {}
    default_resources = config.get("defaultResourceTypes", ["food", "water", "medicine", "shelter"])
    
    regions = []
    errors = []
    warnings = []
    records_processed = 0
    
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        
        for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
            records_processed += 1
            
            # Apply column mapping
            mapped_row = {}
            for key, value in row.items():
                mapped_key = column_mapping.get(key, key).lower().strip()
                mapped_row[mapped_key] = value
            
            # Extract coordinates (handle various naming conventions)
            lat = mapped_row.get("lat") or mapped_row.get("latitude")
            lon = mapped_row.get("lon") or mapped_row.get("longitude") or mapped_row.get("lng")
            
            # Validate
            valid, row_errors = validate_region_data({
                "name": mapped_row.get("name"),
                "population": _safe_float(mapped_row.get("population", 0)),
                "lat": lat,
                "lon": lon,
            })
            
            if not valid:
                errors.append(f"Row {row_num}: {'; '.join(row_errors)}")
                continue
            
            # Parse supply and consumption
            current_supply = {}
            consumption_rate = {}
            population = int(float(mapped_row.get("population", 0)))
            
            for resource in default_resources:
                # Try to find supply column
                supply_key = f"supply_{resource}"
                supply_val = mapped_row.get(supply_key)
                if supply_val:
                    current_supply[resource] = _safe_float(supply_val)
                else:
                    # Default: population * factor
                    current_supply[resource] = population * 0.01
                
                # Try to find consumption column
                consumption_key = f"consumption_{resource}"
                consumption_val = mapped_row.get(consumption_key)
                if consumption_val:
                    consumption_rate[resource] = _safe_float(consumption_val)
                else:
                    consumption_rate[resource] = population * 0.005
            
            regions.append(RegionData(
                name=mapped_row.get("name", "").strip(),
                population=population,
                current_supply=current_supply,
                consumption_rate=consumption_rate,
                risk_factor=_safe_float(mapped_row.get("risk_factor", 0.2)),
                coordinates=(float(lat), float(lon)),
            ))
    
    result = ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        records_processed=records_processed,
        records_valid=len(regions),
    )
    
    logger.info(f"Loaded {len(regions)} regions from {filepath}")
    return regions, result


def load_depots_from_csv(
    filepath: Union[str, Path],
    config: Dict[str, Any],
    column_mapping: Optional[Dict[str, str]] = None,
) -> Tuple[List[Depot], ValidationResult]:
    """
    Load depot data from a CSV file.
    
    Expected CSV columns:
        - name: Depot name (required)
        - lat/latitude: Latitude (required)
        - lon/longitude/lng: Longitude (required)
        - stock_food, stock_water, etc.: Stock per resource (optional)
        - total_stock: Total stock if not per-resource (optional)
    
    Example CSV:
        name,lat,lon,stock_food,stock_water,stock_medicine
        Depot-Toronto,43.70,-79.42,100000,150000,50000
        Depot-Ottawa,45.40,-75.70,80000,120000,40000
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"CSV file not found: {filepath}")
    
    column_mapping = column_mapping or {}
    default_resources = config.get("defaultResourceTypes", ["food", "water", "medicine", "shelter"])
    
    depots = []
    errors = []
    warnings = []
    records_processed = 0
    
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        
        for row_num, row in enumerate(reader, start=2):
            records_processed += 1
            
            mapped_row = {column_mapping.get(k, k).lower().strip(): v for k, v in row.items()}
            
            lat = mapped_row.get("lat") or mapped_row.get("latitude")
            lon = mapped_row.get("lon") or mapped_row.get("longitude") or mapped_row.get("lng")
            
            valid, row_errors = validate_depot_data({
                "name": mapped_row.get("name"),
                "lat": lat,
                "lon": lon,
            })
            
            if not valid:
                errors.append(f"Row {row_num}: {'; '.join(row_errors)}")
                continue
            
            # Parse stock
            stock = {}
            total_stock = _safe_float(mapped_row.get("total_stock", 0))
            
            for resource in default_resources:
                stock_key = f"stock_{resource}"
                stock_val = mapped_row.get(stock_key)
                if stock_val:
                    stock[resource] = _safe_float(stock_val)
                elif total_stock > 0:
                    stock[resource] = total_stock / len(default_resources)
                else:
                    stock[resource] = 10000.0  # Default
            
            depots.append(Depot(
                name=mapped_row.get("name", "").strip(),
                stock=stock,
                coordinates=(float(lat), float(lon)),
            ))
    
    result = ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        records_processed=records_processed,
        records_valid=len(depots),
    )
    
    logger.info(f"Loaded {len(depots)} depots from {filepath}")
    return depots, result


# =============================================================================
# JSON Import Functions
# =============================================================================

def load_regions_from_json(
    filepath: Union[str, Path],
    config: Dict[str, Any],
    data_key: str = "regions",
) -> Tuple[List[RegionData], ValidationResult]:
    """
    Load region data from a JSON file.
    
    Expected JSON structure:
    {
        "regions": [
            {
                "name": "Toronto",
                "population": 2930000,
                "lat": 43.65,
                "lon": -79.38,
                "risk_factor": 0.3,
                "current_supply": {"food": 50000, "water": 80000},
                "consumption_rate": {"food": 5000, "water": 8000}
            },
            ...
        ]
    }
    """
    filepath = Path(filepath)
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    records = data.get(data_key, data) if isinstance(data, dict) else data
    if not isinstance(records, list):
        records = [records]
    
    default_resources = config.get("defaultResourceTypes", ["food", "water", "medicine", "shelter"])
    regions = []
    errors = []
    warnings = []
    
    for i, record in enumerate(records):
        valid, record_errors = validate_region_data(record)
        if not valid:
            errors.append(f"Record {i}: {'; '.join(record_errors)}")
            continue
        
        lat = record.get("lat") or record.get("latitude")
        lon = record.get("lon") or record.get("longitude") or record.get("lng")
        population = int(record.get("population", 0))
        
        current_supply = record.get("current_supply", {})
        consumption_rate = record.get("consumption_rate", {})
        
        for res in default_resources:
            current_supply.setdefault(res, population * 0.01)
            consumption_rate.setdefault(res, population * 0.005)
        
        regions.append(RegionData(
            name=record["name"],
            population=population,
            current_supply=current_supply,
            consumption_rate=consumption_rate,
            risk_factor=float(record.get("risk_factor", 0.2)),
            coordinates=(float(lat), float(lon)),
        ))
    
    return regions, ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        records_processed=len(records),
        records_valid=len(regions),
    )


def load_depots_from_json(
    filepath: Union[str, Path],
    config: Dict[str, Any],
    data_key: str = "depots",
) -> Tuple[List[Depot], ValidationResult]:
    """Load depot data from a JSON file."""
    filepath = Path(filepath)
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    records = data.get(data_key, data) if isinstance(data, dict) else data
    if not isinstance(records, list):
        records = [records]
    
    default_resources = config.get("defaultResourceTypes", ["food", "water", "medicine", "shelter"])
    depots = []
    errors = []
    
    for i, record in enumerate(records):
        valid, record_errors = validate_depot_data(record)
        if not valid:
            errors.append(f"Record {i}: {'; '.join(record_errors)}")
            continue
        
        lat = record.get("lat") or record.get("latitude")
        lon = record.get("lon") or record.get("longitude") or record.get("lng")
        
        stock = record.get("stock", {})
        for res in default_resources:
            stock.setdefault(res, 10000.0)
        
        depots.append(Depot(
            name=record["name"],
            stock=stock,
            coordinates=(float(lat), float(lon)),
        ))
    
    return depots, ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=[],
        records_processed=len(records),
        records_valid=len(depots),
    )


# =============================================================================
# API Integration
# =============================================================================

class RealDataLoader:
    """
    Loader for fetching real data from external APIs.
    
    Example:
        loader = RealDataLoader(config)
        regions = loader.fetch_regions_from_api(
            "https://api.example.com/regions",
            headers={"Authorization": "Bearer token"}
        )
    """
    
    def __init__(self, config: Dict[str, Any], timeout: int = 30):
        self.config = config
        self.timeout = timeout
        self.default_resources = config.get("defaultResourceTypes", ["food", "water", "medicine", "shelter"])
    
    def fetch_regions_from_api(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        data_key: str = "regions",
        transform_fn: Optional[Callable[[Dict], Dict]] = None,
    ) -> Tuple[List[RegionData], List[str]]:
        """
        Fetch region data from a REST API.
        
        Args:
            url: API endpoint URL
            headers: HTTP headers (e.g., for authentication)
            params: Query parameters
            data_key: Key in response JSON containing the data array
            transform_fn: Optional function to transform each record
        
        Returns:
            Tuple of (regions, warnings)
        """
        if not HAS_REQUESTS:
            raise ImportError("requests library required for API fetching")
        
        response = requests.get(url, headers=headers, params=params, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        
        records = data.get(data_key, data) if isinstance(data, dict) else data
        if not isinstance(records, list):
            records = [records]
        
        regions = []
        warnings = []
        
        for record in records:
            if transform_fn:
                record = transform_fn(record)
            
            valid, errors = validate_region_data(record)
            if not valid:
                warnings.append(f"Skipped invalid record: {errors}")
                continue
            
            lat = record.get("lat") or record.get("latitude")
            lon = record.get("lon") or record.get("longitude") or record.get("lng")
            population = int(record.get("population", 0))
            
            current_supply = record.get("current_supply", {})
            consumption_rate = record.get("consumption_rate", {})
            
            for res in self.default_resources:
                current_supply.setdefault(res, population * 0.01)
                consumption_rate.setdefault(res, population * 0.005)
            
            regions.append(RegionData(
                name=record["name"],
                population=population,
                current_supply=current_supply,
                consumption_rate=consumption_rate,
                risk_factor=float(record.get("risk_factor", 0.2)),
                coordinates=(float(lat), float(lon)),
            ))
        
        return regions, warnings
    
    def fetch_depots_from_api(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        data_key: str = "depots",
        transform_fn: Optional[Callable[[Dict], Dict]] = None,
    ) -> Tuple[List[Depot], List[str]]:
        """Fetch depot data from a REST API."""
        if not HAS_REQUESTS:
            raise ImportError("requests library required for API fetching")
        
        response = requests.get(url, headers=headers, params=params, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        
        records = data.get(data_key, data) if isinstance(data, dict) else data
        if not isinstance(records, list):
            records = [records]
        
        depots = []
        warnings = []
        
        for record in records:
            if transform_fn:
                record = transform_fn(record)
            
            valid, errors = validate_depot_data(record)
            if not valid:
                warnings.append(f"Skipped invalid depot: {errors}")
                continue
            
            lat = record.get("lat") or record.get("latitude")
            lon = record.get("lon") or record.get("longitude") or record.get("lng")
            
            stock = record.get("stock", {})
            for res in self.default_resources:
                stock.setdefault(res, 10000.0)
            
            depots.append(Depot(
                name=record["name"],
                stock=stock,
                coordinates=(float(lat), float(lon)),
            ))
        
        return depots, warnings


# =============================================================================
# Pandas Integration (if available)
# =============================================================================

if HAS_PANDAS:
    def load_regions_from_dataframe(
        df: "pd.DataFrame",
        config: Dict[str, Any],
        column_mapping: Optional[Dict[str, str]] = None,
    ) -> Tuple[List[RegionData], ValidationResult]:
        """
        Load region data from a pandas DataFrame.
        
        Example:
            import pandas as pd
            df = pd.read_excel("regions.xlsx")
            regions, result = load_regions_from_dataframe(df, config)
        """
        column_mapping = column_mapping or {}
        df = df.rename(columns={v: k for k, v in column_mapping.items()})
        
        default_resources = config.get("defaultResourceTypes", ["food", "water", "medicine", "shelter"])
        regions = []
        errors = []
        
        for idx, row in df.iterrows():
            lat = row.get("lat") or row.get("latitude")
            lon = row.get("lon") or row.get("longitude") or row.get("lng")
            
            if pd.isna(row.get("name")) or pd.isna(lat) or pd.isna(lon):
                errors.append(f"Row {idx}: Missing required fields")
                continue
            
            population = int(row.get("population", 0))
            
            current_supply = {}
            consumption_rate = {}
            for res in default_resources:
                supply_col = f"supply_{res}"
                consumption_col = f"consumption_{res}"
                current_supply[res] = float(row.get(supply_col, population * 0.01))
                consumption_rate[res] = float(row.get(consumption_col, population * 0.005))
            
            regions.append(RegionData(
                name=str(row["name"]),
                population=population,
                current_supply=current_supply,
                consumption_rate=consumption_rate,
                risk_factor=float(row.get("risk_factor", 0.2)),
                coordinates=(float(lat), float(lon)),
            ))
        
        return regions, ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=[],
            records_processed=len(df),
            records_valid=len(regions),
        )


# =============================================================================
# Historical Data for Time-Series
# =============================================================================

def load_historical_supply_data(
    filepath: Union[str, Path],
    region_name: str,
) -> List[Dict[str, float]]:
    """
    Load historical supply/consumption data for time-series forecasting.
    
    Expected CSV format:
        date,region,supply,consumption
        2024-01-01,Toronto,50000,45000
        2024-01-02,Toronto,48000,46000
        ...
    
    Returns:
        List of {"day": int, "supply": float, "consumption": float}
    """
    filepath = Path(filepath)
    history = []
    
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("region") == region_name:
                history.append({
                    "day": len(history),
                    "supply": float(row.get("supply", 0)),
                    "consumption": float(row.get("consumption", 0)),
                })
    
    return history


# =============================================================================
# Helper Functions
# =============================================================================

def _safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert value to float."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# =============================================================================
# Example Data Templates
# =============================================================================

EXAMPLE_REGIONS_CSV = """name,population,lat,lon,risk_factor,supply_food,supply_water,consumption_food,consumption_water
Toronto,2930000,43.6532,-79.3832,0.3,50000,80000,5000,8000
Ottawa,1017449,45.4215,-75.6972,0.2,30000,45000,3000,4500
Mississauga,721599,43.5890,-79.6441,0.25,25000,40000,2500,4000
Hamilton,569353,43.2557,-79.8711,0.35,20000,35000,2000,3500
London,422324,42.9849,-81.2453,0.3,18000,30000,1800,3000
"""

EXAMPLE_DEPOTS_CSV = """name,lat,lon,stock_food,stock_water,stock_medicine,stock_shelter
Depot-Toronto,43.7000,-79.4000,100000,150000,50000,30000
Depot-Ottawa,45.4000,-75.7000,80000,120000,40000,25000
Depot-London,43.0000,-81.2000,60000,90000,30000,20000
"""

EXAMPLE_REGIONS_JSON = {
    "regions": [
        {
            "name": "Toronto",
            "population": 2930000,
            "lat": 43.6532,
            "lon": -79.3832,
            "risk_factor": 0.3,
            "current_supply": {"food": 50000, "water": 80000, "medicine": 20000, "shelter": 10000},
            "consumption_rate": {"food": 5000, "water": 8000, "medicine": 2000, "shelter": 1000}
        },
        {
            "name": "Ottawa",
            "population": 1017449,
            "lat": 45.4215,
            "lon": -75.6972,
            "risk_factor": 0.2,
            "current_supply": {"food": 30000, "water": 45000, "medicine": 12000, "shelter": 6000},
            "consumption_rate": {"food": 3000, "water": 4500, "medicine": 1200, "shelter": 600}
        }
    ],
    "metadata": {
        "source": "Example data",
        "last_updated": "2024-01-15T10:00:00Z"
    }
}


def create_example_files(output_dir: Union[str, Path] = ".") -> None:
    """Create example CSV and JSON files for testing."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # CSV files
    (output_dir / "example_regions.csv").write_text(EXAMPLE_REGIONS_CSV)
    (output_dir / "example_depots.csv").write_text(EXAMPLE_DEPOTS_CSV)
    
    # JSON file
    (output_dir / "example_regions.json").write_text(json.dumps(EXAMPLE_REGIONS_JSON, indent=2))
    
    print(f"Created example files in {output_dir}")
