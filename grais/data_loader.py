from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from grais.data_generator import RegionData, Depot


DATA_DIR = Path(__file__).resolve().parent.parent / "config"


def load_real_ontario_regions(config: Dict) -> Tuple[List[RegionData], List[str]]:
    warnings: List[str] = []
    path = DATA_DIR / "ontario_real_regions.json"
    if not path.exists():
        raise FileNotFoundError("Real region data not found")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    regions: List[RegionData] = []
    default_resources = config.get("defaultResourceTypes", [])
    for entry in data.get("regions", []):
        name = entry.get("name")
        if not name:
            warnings.append("Region missing name; skipped")
            continue
        population = int(entry.get("population", 0))
        lat, lon = entry.get("lat"), entry.get("lon")
        if lat is None or lon is None:
            warnings.append(f"Region {name} missing coordinates; skipped")
            continue
        risk_factor = float(entry.get("risk_factor", 0.2))
        current_supply = entry.get("current_supply", {})
        consumption_rate = entry.get("consumption_rate", {})
        # Fill missing resource keys
        for res in default_resources:
            current_supply.setdefault(res, population * 0.01)
            consumption_rate.setdefault(res, population * 0.005)
        regions.append(
            RegionData(
                name=name,
                population=population,
                current_supply=current_supply,
                consumption_rate=consumption_rate,
                risk_factor=risk_factor,
                coordinates=(lat, lon),
            )
        )
    if not regions:
        warnings.append("No valid regions loaded; falling back to synthetic")
        raise ValueError("No valid regions loaded")
    return regions, warnings


def load_real_ontario_depots(config: Dict) -> Tuple[List[Depot], List[str]]:
    warnings: List[str] = []
    path = DATA_DIR / "ontario_real_depots.json"
    if not path.exists():
        raise FileNotFoundError("Real depot data not found")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    depots: List[Depot] = []
    default_resources = config.get("defaultResourceTypes", [])
    for entry in data.get("depots", []):
        name = entry.get("name")
        if not name:
            warnings.append("Depot missing name; skipped")
            continue
        lat, lon = entry.get("lat"), entry.get("lon")
        if lat is None or lon is None:
            warnings.append(f"Depot {name} missing coordinates; skipped")
            continue
        stock = entry.get("stock", {})
        for res in default_resources:
            stock.setdefault(res, 5_000.0)
        depots.append(Depot(name=name, stock=stock, coordinates=(lat, lon)))
    if not depots:
        warnings.append("No valid depots loaded; falling back to synthetic")
        raise ValueError("No valid depots loaded")
    return depots, warnings
