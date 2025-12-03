"""
What-if comparison smoke test.
Run: python scripts/smoke_scenarios.py
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
from grais.prioritization import prioritize_regions
from main import apply_scenario


def summarize(allocation):
    unmet_total = sum(item.get("unmet", 0) for item in allocation.get("unmet", []))
    return {"objective": allocation.get("objective"), "unmet_total": round(unmet_total, 2), "status": allocation.get("solverStatus")}


def run():
    cfg = load_config("ontario")
    regions = generate_regions_from_config(cfg)
    preds = predict_shortages(regions, cfg["timeHorizonDays"], cfg["mode"], cfg)
    depots = generate_supply_depots(cfg)
    supplies = {d.name: d.stock for d in depots}
    distances = build_distance_matrix(depots, regions)
    demands = generate_multi_resource_demands(preds, cfg)

    scenarios = [
        {"name": "Baseline", "payload": None},
        {"name": "DepotOut", "payload": {"depotOutages": ["Depot-1"]}},
        {"name": "BudgetCut", "payload": {"budgetLimit": 40000}},
        {"name": "Surge", "payload": {"demandSurgeMultiplier": 1.3}},
    ]

    for s in scenarios:
        supplies_adj, demands_adj, distances_adj, cfg_adj = apply_scenario(supplies, demands, distances, cfg, s["payload"])
        allocation = optimize_allocation(supplies_adj, demands_adj, distances_adj, cfg_adj["mode"], cfg_adj)
        priorities = prioritize_regions(preds, allocation, cfg_adj["mode"], cfg_adj)
        print(f"\nScenario: {s['name']}")
        print("Summary:", summarize(allocation))
        print("Top priorities:", priorities[:2])


if __name__ == "__main__":
    run()
