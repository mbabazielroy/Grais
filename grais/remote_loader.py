from __future__ import annotations

import json
import time
from typing import Dict, List, Tuple

import requests

from grais.data_generator import RegionData, Depot

_CACHE: Dict[str, Dict] = {}
_CACHE_TTL = 3600  # seconds


def fetch_remote_regions(url: str, config: Dict, timeout: int = 10) -> Tuple[List[RegionData], List[str], Dict]:
    warnings: List[str] = []
    data, meta = _get_cached(url, timeout)
    regions: List[RegionData] = []
    default_resources = config.get("defaultResourceTypes", [])
    hazard = meta.get("hazard_index", None)
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
        if hazard is not None:
            risk_factor = min(1.0, max(0.0, (risk_factor + hazard) / 2))
        current_supply = entry.get("current_supply", {})
        consumption_rate = entry.get("consumption_rate", {})
        if not isinstance(current_supply, dict) or not isinstance(consumption_rate, dict):
            warnings.append(f"Region {name} invalid supply/consumption; skipped")
            continue
        for res in default_resources:
            current_supply.setdefault(res, population * 0.01)
            consumption_rate.setdefault(res, population * 0.005)
        regions.append(
            RegionData(
                name=name,
                population=population,
                current_supply=current_supply,
                consumption_rate=consumption_rate,
                risk_factor=order_risk(risk_factor),
                coordinates=(lat, lon),
            )
        )
    if not regions:
        warnings.append("No valid regions loaded from remote")
        raise ValueError("No valid regions loaded from remote")
    return regions, warnings, meta


def fetch_remote_depots(url: str, config: Dict, timeout: int = 10) -> Tuple[List[Depot], List[str], Dict]:
    warnings: List[str] = []
    data, meta = _get_cached(url, timeout)
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
        if not isinstance(stock, dict):
            warnings.append(f"Depot {name} invalid stock; skipped")
            continue
        for res in default_resources:
            stock.setdefault(res, 5_000.0)
        depots.append(Depot(name=name, stock=stock, coordinates=(lat, lon)))
    if not depots:
        warnings.append("No valid depots loaded from remote")
        raise ValueError("No valid depots loaded from remote")
    return depots, warnings, meta


def order_risk(value: float) -> float:
    # Clamp risk between 0 and 1
    return max(0.0, min(1.0, value))


def _get_cached(url: str, timeout: int) -> Tuple[Dict, Dict]:
    now = time.time()
    if url in _CACHE and now - _CACHE[url]["time"] < _CACHE_TTL:
        return _CACHE[url]["data"], _CACHE[url]["meta"]
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    meta = {
        "last_updated": resp.headers.get("Last-Modified") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "timestamp": now,
    }
    _CACHE[url] = {"data": data, "time": now, "meta": meta}
    return data, meta
