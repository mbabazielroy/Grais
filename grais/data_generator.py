import math
import random
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np


@dataclass
class RegionData:
    name: str
    population: int
    current_supply: Dict[str, float]
    consumption_rate: Dict[str, float]
    risk_factor: float
    coordinates: Tuple[float, float]


@dataclass
class Depot:
    name: str
    stock: Dict[str, float]
    coordinates: Tuple[float, float]


# Approximate centroids for common countries to avoid map clustering when coordinates are missing.
COUNTRY_CENTROIDS: Dict[str, Tuple[float, float]] = {
    "canada": (56.1304, -106.3468),
    "united states": (37.0902, -95.7129),
    "usa": (37.0902, -95.7129),
    "mexico": (23.6345, -102.5528),
    "brazil": (-14.235, -51.9253),
    "nigeria": (9.082, 8.6753),
    "kenya": (-0.0236, 37.9062),
    "south africa": (-30.5595, 22.9375),
    "india": (20.5937, 78.9629),
    "china": (35.8617, 104.1954),
    "philippines": (12.8797, 121.774),
    "indonesia": (-0.7893, 113.9213),
    "germany": (51.1657, 10.4515),
    "united kingdom": (55.3781, -3.436),
    "france": (46.2276, 2.2137),
    "turkey": (38.9637, 35.2433),
    "saudi arabia": (23.8859, 45.0792),
    "australia": (-25.2744, 133.7751),
    "japan": (36.2048, 138.2529),
    "egypt": (26.8206, 30.8025),
    "ethiopia": (9.145, 40.4897),
    "argentina": (-38.4161, -63.6167),
    "spain": (40.4637, -3.7492),
    "italy": (41.8719, 12.5674),
}

def _random_near(value: float, spread: float, rng: random.Random) -> float:
    return max(0.0, rng.normalvariate(value, spread))


def generate_regions_from_config(config: Dict, seed: int = 42) -> List[RegionData]:
    rng = random.Random(seed)
    regions = []
    base_pop = 3_000_000 if config["mode"] == "global" else 700_000
    coord_map = config.get("regionCoords", {})

    for idx, region_name in enumerate(config.get("regions", [])):
        population = int(base_pop * _random_near(1.0 + idx * 0.05, 0.2, rng))
        consumption_rate = {
            res: _random_near(1.5, 0.4, rng) * population * 0.001
            for res in config.get("defaultResourceTypes", [])
        }
        current_supply = {
            res: consumption_rate[res] * rng.uniform(2, 6) for res in consumption_rate
        }
        risk_factor = min(1.0, max(0.05, rng.uniform(0.1, 0.8)))
        key = region_name.lower()
        if region_name in coord_map:
            coordinates = (coord_map[region_name]["lat"], coord_map[region_name]["lon"])
        elif key in COUNTRY_CENTROIDS:
            coordinates = COUNTRY_CENTROIDS[key]
        else:
            if config.get("mode") == "regional":
                lat_offset = rng.uniform(-3, 3)
                lon_offset = rng.uniform(-3, 3)
                coordinates = (config.get("baseLat", 45.0) + lat_offset, config.get("baseLon", -75.0) + lon_offset)
            else:
                # Spread coordinates deterministically across globe to avoid clustering.
                hrng = random.Random(seed + hash(region_name) % 10_000)
                lat = hrng.uniform(-55, 65)  # avoid poles for simple placement
                lon = hrng.uniform(-170, 170)
                coordinates = (lat, lon)

        regions.append(
            RegionData(
                name=region_name,
                population=population,
                current_supply=current_supply,
                consumption_rate=consumption_rate,
                risk_factor=risk_factor,
                coordinates=coordinates,
            )
        )
    return regions


def generate_supply_depots(config: Dict, seed: int = 7) -> List[Depot]:
    rng = random.Random(seed)
    depots = []
    for i in range(config.get("depots", 3)):
        stock = {
            res: rng.uniform(2_000, 8_000) if config["mode"] == "regional" else rng.uniform(20_000, 80_000)
            for res in config.get("defaultResourceTypes", [])
        }
        coordinates = (
            config.get("baseLat", 45.0) + rng.uniform(-2, 2),
            config.get("baseLon", -75.0) + rng.uniform(-2, 2),
        )
        depots.append(Depot(name=f"Depot-{i+1}", stock=stock, coordinates=coordinates))
    return depots


def haversine_km(coord1: Tuple[float, float], coord2: Tuple[float, float]) -> float:
    """Rough distance in km for sizing transport costs."""
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    r = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def build_distance_matrix(depots: List[Depot], regions: List[RegionData]) -> Dict[str, Dict[str, float]]:
    matrix: Dict[str, Dict[str, float]] = {}
    for depot in depots:
        matrix[depot.name] = {}
        for region in regions:
            matrix[depot.name][region.name] = haversine_km(depot.coordinates, region.coordinates)
    return matrix


def generate_demands_from_predictions(
    predictions: List[Dict], config: Dict, resource: str = "food"
) -> Dict[str, float]:
    # Convert shortage predictions to demand units for optimization
    demands: Dict[str, float] = {}
    for pred in predictions:
        severity_multiplier = {"low": 0.8, "medium": 1.0, "high": 1.4}.get(pred["shortageSeverity"], 1.0)
        base_demand = pred["population"] * 0.02 if config["mode"] == "regional" else pred["population"] * 0.01
        demands[pred["region"]] = base_demand * severity_multiplier
    return demands


def generate_multi_resource_demands(predictions: List[Dict], config: Dict) -> Dict[str, Dict[str, float]]:
    """
    Build per-resource demands for each region based on predictions.
    """
    demands: Dict[str, Dict[str, float]] = {res: {} for res in config.get("defaultResourceTypes", [])}
    for pred in predictions:
        base = pred["population"] * (0.025 if config["mode"] == "regional" else 0.012)
        severity_multiplier = {"low": 0.8, "medium": 1.0, "high": 1.4}.get(pred["shortageSeverity"], 1.0)
        for res in demands:
            demands[res][pred["region"]] = base * severity_multiplier
    return demands


def generate_supply_history(region: RegionData, days: int = 60, seed: int = 99) -> List[Dict[str, float]]:
    rng = random.Random(seed + hash(region.name) % 1000)
    history = []
    base_supply = sum(region.current_supply.values()) / len(region.current_supply) if region.current_supply else 1.0
    base_cons = sum(region.consumption_rate.values()) / len(region.consumption_rate) if region.consumption_rate else 1.0
    trend = rng.uniform(-0.05, 0.05)  # daily drift
    for day in range(days):
        noise = rng.uniform(-0.1, 0.1)
        supply = max(0.0, base_supply * (1 + trend * day + noise))
        consumption = max(0.0, base_cons * (1 + 0.01 * day + rng.uniform(-0.05, 0.05)))
        history.append({"day": day, "supply": supply, "consumption": consumption})
    return history
