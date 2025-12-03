import pytest

from grais.config_loader import load_config
from grais.data_generator import generate_regions_from_config, generate_supply_depots, build_distance_matrix, generate_multi_resource_demands
from grais.prediction import predict_shortages
from grais.optimization import optimize_allocation
from grais.prioritization import prioritize_regions


def test_pipeline_smoke():
    cfg = load_config("ontario")
    regions = generate_regions_from_config(cfg)
    preds = predict_shortages(regions, cfg["timeHorizonDays"], cfg["mode"], cfg)
    depots = generate_supply_depots(cfg)
    supplies = {d.name: d.stock for d in depots}
    distances = build_distance_matrix(depots, regions)
    demands = generate_multi_resource_demands(preds, cfg)

    allocation = optimize_allocation(supplies, demands, distances, cfg["mode"], cfg)
    priorities = prioritize_regions(preds, allocation, cfg["mode"], cfg)

    assert preds and allocation.get("shipments") and priorities
    assert allocation.get("solverStatus")
