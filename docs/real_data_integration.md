# Real Data Integration Guide

This guide explains how to integrate real-world data into GRAIS instead of using synthetic data.

## Quick Start

### Option 1: CSV Files

```python
from grais.real_data import load_regions_from_csv, load_depots_from_csv
from grais.config_loader import load_config

config = load_config("ontario")

# Load regions from CSV
regions, result = load_regions_from_csv("data/example_regions.csv", config)
print(f"Loaded {result.records_valid} regions")

# Load depots from CSV
depots, result = load_depots_from_csv("data/example_depots.csv", config)
print(f"Loaded {result.records_valid} depots")
```

### Option 2: JSON Files

```python
from grais.real_data import load_regions_from_json, load_depots_from_json

regions, result = load_regions_from_json("data/example_regions.json", config)
depots, result = load_depots_from_json("data/example_depots.json", config)
```

### Option 3: REST API

```python
from grais.real_data import RealDataLoader

loader = RealDataLoader(config)

# Fetch from your API
regions, warnings = loader.fetch_regions_from_api(
    "https://api.yourorganization.com/regions",
    headers={"Authorization": "Bearer YOUR_API_KEY"}
)

depots, warnings = loader.fetch_depots_from_api(
    "https://api.yourorganization.com/depots",
    headers={"Authorization": "Bearer YOUR_API_KEY"}
)
```

### Option 4: Pandas DataFrame

```python
import pandas as pd
from grais.real_data import load_regions_from_dataframe

# Load from Excel, database, or any pandas source
df = pd.read_excel("regions.xlsx")
regions, result = load_regions_from_dataframe(df, config)
```

## Data Format Requirements

### Region Data

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | Yes | string | Region identifier |
| `population` | Yes | integer | Population count |
| `lat` / `latitude` | Yes | float | Latitude (-90 to 90) |
| `lon` / `longitude` / `lng` | Yes | float | Longitude (-180 to 180) |
| `risk_factor` | No | float | Vulnerability score (0-1), default 0.2 |
| `current_supply` | No | dict | Supply per resource type |
| `consumption_rate` | No | dict | Daily consumption per resource |
| `supply_food`, `supply_water`, etc. | No | float | Alternative supply format |
| `consumption_food`, etc. | No | float | Alternative consumption format |

### Depot Data

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | Yes | string | Depot identifier |
| `lat` / `latitude` | Yes | float | Latitude |
| `lon` / `longitude` / `lng` | Yes | float | Longitude |
| `stock` | No | dict | Stock per resource type |
| `stock_food`, `stock_water`, etc. | No | float | Alternative stock format |

## Example CSV Format

### regions.csv
```csv
name,population,lat,lon,risk_factor,supply_food,supply_water,consumption_food,consumption_water
Toronto,2930000,43.6532,-79.3832,0.3,50000,80000,5000,8000
Ottawa,1017449,45.4215,-75.6972,0.2,30000,45000,3000,4500
```

### depots.csv
```csv
name,lat,lon,stock_food,stock_water,stock_medicine,stock_shelter
Depot-Toronto,43.7000,-79.4000,100000,150000,50000,30000
Depot-Ottawa,45.4000,-75.7000,80000,120000,40000,25000
```

## Example JSON Format

### regions.json
```json
{
  "regions": [
    {
      "name": "Toronto",
      "population": 2930000,
      "lat": 43.6532,
      "lon": -79.3832,
      "risk_factor": 0.3,
      "current_supply": {"food": 50000, "water": 80000},
      "consumption_rate": {"food": 5000, "water": 8000}
    }
  ]
}
```

## Column Mapping

If your data has different column names, use the `column_mapping` parameter:

```python
regions, result = load_regions_from_csv(
    "my_data.csv",
    config,
    column_mapping={
        "region_name": "name",
        "pop": "population", 
        "latitude": "lat",
        "longitude": "lon",
        "vulnerability": "risk_factor"
    }
)
```

## Using Real Data with the API

### Method 1: Configure Remote Data Source

Update your config file to point to real data:

```json
{
  "mode": "regional",
  "region": "Ontario",
  "dataSource": "remote",
  "remoteRegionsUrl": "https://api.yourorg.com/regions",
  "remoteDepotsUrl": "https://api.yourorg.com/depots"
}
```

Then call the API with `dataSource: "remote"`:

```bash
curl -X POST http://localhost:8000/recommend \
  -H "Content-Type: application/json" \
  -d '{"configName": "ontario", "dataSource": "remote"}'
```

### Method 2: Direct Integration

Modify `main.py` to use your data loader:

```python
from grais.real_data import load_regions_from_csv, load_depots_from_csv

def _get_regions_and_depots(cfg, data_source, custom_regions=None):
    if data_source == "csv":
        regions, r_result = load_regions_from_csv("path/to/regions.csv", cfg)
        depots, d_result = load_depots_from_csv("path/to/depots.csv", cfg)
        warnings = r_result.errors + d_result.errors
        return regions, depots, warnings, "csv", {}
    # ... rest of function
```

## Data Sources Examples

### Ontario Open Data
- Population: https://data.ontario.ca/dataset/population-projections
- Health Units: https://data.ontario.ca/dataset/public-health-unit-boundaries

### Statistics Canada
- Census data: https://www12.statcan.gc.ca/census-recensement/

### OpenStreetMap
- Warehouse locations: Overpass API queries

### Humanitarian Data Exchange
- https://data.humdata.org/

## Time-Series Data for Forecasting

To enable Prophet time-series forecasting with real historical data:

```python
from grais.real_data import load_historical_supply_data

# Load historical data
history = load_historical_supply_data("historical_supply.csv", "Toronto")

# Use with forecaster
from grais.forecasting import TimeSeriesForecaster
forecaster = TimeSeriesForecaster(use_prophet=True)
forecast = forecaster.forecast_gap(history, horizon_days=7)
```

Historical data CSV format:
```csv
date,region,supply,consumption
2024-01-01,Toronto,50000,45000
2024-01-02,Toronto,48000,46000
2024-01-03,Toronto,47000,44000
```

## Validation

All data loaders return a `ValidationResult`:

```python
regions, result = load_regions_from_csv("data.csv", config)

print(f"Valid: {result.valid}")
print(f"Records processed: {result.records_processed}")
print(f"Records valid: {result.records_valid}")
print(f"Errors: {result.errors}")
print(f"Warnings: {result.warnings}")
```

## Troubleshooting

### "Missing coordinates"
Ensure your data has `lat`/`lon` or `latitude`/`longitude` columns.

### "Invalid population"
Population must be a positive number.

### "No valid regions loaded"
Check that your file path is correct and data format matches expected schema.

### API returns empty data
Check authentication headers and API endpoint URL.
