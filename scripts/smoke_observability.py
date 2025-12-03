"""
Lightweight QA smoke to exercise prediction -> optimization -> prioritization and metrics.
Run: python scripts/smoke_observability.py
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
from grais.metrics import metrics


def run():
    cfg = load_config("ontario")
    regions = generate_regions_from_config(cfg)
    preds = predict_shortages(regions, cfg["timeHorizonDays"], cfg["mode"], cfg)
    depots = generate_supply_depots(cfg)
    supplies = {d.name: d.stock for d in depots}
    distances = build_distance_matrix(depots, regions)
    demands = generate_multi_resource_demands(preds, cfg)

    allocation = optimize_allocation(supplies, demands, distances, cfg["mode"], cfg)
    metrics.record_solver_status(allocation.get("solverStatus", "UNKNOWN"))
    metrics.record_unmet(sum(item.get("unmet", 0) for item in allocation.get("unmet", [])))

    priorities = prioritize_regions(preds, allocation, cfg["mode"], cfg)

    assert priorities, "Priorities should not be empty"
    assert allocation.get("shipments"), "Shipments should be produced"
    print("Solver status:", allocation.get("solverStatus"))
    print("Shipments sample:", allocation["shipments"][:3])
    print("Priorities sample:", priorities[:3])
    print("Metrics snapshot:", metrics.snapshot())


if __name__ == "__main__":
    run()
