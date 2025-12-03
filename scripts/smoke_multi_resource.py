"""
Quick smoke test for multi-resource optimization with scenarios.
Run: python scripts/smoke_multi_resource.py
"""
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from grais.config_loader import load_config
from grais.data_generator import (
    generate_regions_from_config,
    generate_supply_depots,
    build_distance_matrix,
    generate_multi_resource_demands,
)
from grais.prediction import predict_shortages
from grais.optimization import optimize_allocation
from main import apply_scenario


def run():
    cfg = load_config("ontario")
    regions = generate_regions_from_config(cfg)
    preds = predict_shortages(regions, cfg["timeHorizonDays"], cfg["mode"], cfg)
    depots = generate_supply_depots(cfg)
    supplies = {d.name: d.stock for d in depots}
    distances = build_distance_matrix(depots, regions)
    demands = generate_multi_resource_demands(preds, cfg)

    scenario = {
        "depotOutages": ["Depot-1"],
        "demandSurgeMultiplier": 1.1,
        "budgetLimit": 60000,
    }

    supplies_adj, demands_adj, distances_adj, cfg_adj = apply_scenario(supplies, demands, distances, cfg, scenario)
    allocation = optimize_allocation(supplies_adj, demands_adj, distances_adj, cfg_adj["mode"], cfg_adj)

    print("Shipments (sample):", allocation["shipments"][:5])
    print("Unmet (aggregate):", allocation["unmet"][:5])
    print("Objective:", allocation["objective"])


if __name__ == "__main__":
    run()
