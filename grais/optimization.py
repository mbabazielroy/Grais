from __future__ import annotations

from typing import Dict, List, Tuple, Union

try:
    from ortools.linear_solver import pywraplp
except Exception:  # pragma: no cover
    pywraplp = None

try:
    import pulp
except Exception:  # pragma: no cover
    pulp = None


def optimize_allocation(
    supplies: Union[Dict[str, float], Dict[str, Dict[str, float]]],
    demands: Union[Dict[str, float], Dict[str, Dict[str, float]]],
    distances: Dict[str, Dict[str, float]],
    mode: str,
    config: Dict,
) -> Dict:
    """
    Dispatch to single- or multi-resource optimizer based on the input structure.
    """
    if supplies and isinstance(next(iter(supplies.values())), dict):
        return _optimize_multi_resource(
            supplies=typing_cast(Dict[str, Dict[str, float]], supplies),
            demands=typing_cast(Dict[str, Dict[str, float]], demands),
            distances=distances,
            mode=mode,
            config=config,
        )
    return _optimize_single_resource(
        typing_cast(Dict[str, float], supplies),
        typing_cast(Dict[str, float], demands),
        distances,
        mode,
        config,
    )


def multi_period_plan(
    supplies: Dict[str, Dict[str, float]],
    demands: Dict[str, Dict[str, float]],
    distances: Dict[str, Dict[str, float]],
    mode: str,
    config: Dict,
    periods: int = 1,
) -> Dict:
    remaining_supplies = {d: stock.copy() for d, stock in supplies.items()}
    all_shipments = []
    unmet_agg: Dict[str, float] = {}
    objectives = []
    constraints = None
    for t in range(periods):
        result = optimize_allocation(remaining_supplies, demands, distances, mode, config)
        objectives.append(result.get("objective"))
        constraints = result.get("constraints", constraints)
        # subtract shipped amounts
        for shipment in result.get("shipments", []):
            depot = shipment["from"]
            res = shipment.get("resource")
            qty = shipment["quantity"]
            if depot in remaining_supplies:
                if res and res in remaining_supplies[depot]:
                    remaining_supplies[depot][res] = max(0.0, remaining_supplies[depot][res] - qty)
                else:
                    # single-resource fallback
                    key = next(iter(remaining_supplies[depot]))
                    remaining_supplies[depot][key] = max(0.0, remaining_supplies[depot][key] - qty)
            shipment["period"] = t + 1
            all_shipments.append(shipment)
        for u in result.get("unmet", []):
            unmet_agg[u["region"]] = unmet_agg.get(u["region"], 0) + u["unmet"]
    return {
        "shipments": all_shipments,
        "unmet": [{"region": r, "unmet": round(v, 2)} for r, v in unmet_agg.items()],
        "objective": sum(obj for obj in objectives if obj is not None),
        "constraints": constraints,
        "mode": mode,
        "solverStatus": "MultiPeriod",
        "periods": periods,
    }


# Simple cast helper to avoid mypy complaints without importing typing_extensions.
def typing_cast(tp, value):
    return value


def _optimize_single_resource(
    supplies: Dict[str, float],
    demands: Dict[str, float],
    distances: Dict[str, Dict[str, float]],
    mode: str,
    config: Dict,
) -> Dict:
    """
    Linear programming allocation minimizing distance while meeting demand.

    Args:
        supplies: map of depot -> available units.
        demands: map of region -> required units.
        distances: depot -> region -> km.
    """
    if pywraplp is None:
        return _optimize_single_resource_pulp(supplies, demands, distances, mode, config)

    solver = pywraplp.Solver.CreateSolver("SCIP")
    if solver is None:
        return _optimize_single_resource_pulp(supplies, demands, distances, mode, config)

    x: Dict[Tuple[str, str], pywraplp.Variable] = {}
    for depot, regions in distances.items():
        for region in demands:
            dist = regions.get(region, 1e6)
            max_distance = config.get("maxDistanceKm", 5_000)
            # Skip infeasible edges beyond max distance
            if dist > max_distance:
                continue
            x[(depot, region)] = solver.NumVar(0, solver.infinity(), f"x_{depot}_{region}")

    # Supply constraints
    for depot, supply in supplies.items():
        solver.Add(sum(var for (d, _), var in x.items() if d == depot) <= supply)

    # Demand constraints (allow under-supply but penalize)
    unmet = {}
    for region, demand in demands.items():
        assigned = sum(var for (d, r), var in x.items() if r == region)
        unmet[region] = solver.NumVar(0, solver.infinity(), f"unmet_{region}")
        solver.Add(assigned + unmet[region] >= demand)

    # Objective: minimize transport + heavy penalty on unmet demand (prioritize)
    transport_cost = []
    for (depot, region), var in x.items():
        dist = distances[depot][region]
        transport_cost.append(dist * var)
    penalty_cost = []
    for region, u in unmet.items():
        penalty = 1000.0 if mode == "regional" else 500.0
        penalty_cost.append(penalty * u)

    solver.Minimize(solver.Sum(transport_cost + penalty_cost))

    status = solver.Solve()
    status_name = _status_name(status)
    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        raise RuntimeError("Allocation problem infeasible")

    shipments = []
    for (depot, region), var in x.items():
        qty = var.solution_value()
        if qty <= 0:
            continue
        shipments.append(
            {
                "from": depot,
                "to": region,
                "quantity": round(qty, 2),
                "distanceKm": round(distances[depot][region], 2),
            }
        )

    unmet_list = []
    for region, u in unmet.items():
        if u.solution_value() > 0:
            unmet_list.append({"region": region, "unmet": round(u.solution_value(), 2)})

    return {
        "shipments": shipments,
        "unmet": unmet_list,
        "objective": round(solver.Objective().Value(), 2),
        "mode": mode,
        "solverStatus": status_name,
    }


def _optimize_multi_resource(
    supplies: Dict[str, Dict[str, float]],
    demands: Dict[str, Dict[str, float]],
    distances: Dict[str, Dict[str, float]],
    mode: str,
    config: Dict,
) -> Dict:
    if pywraplp is None:
        return _optimize_multi_resource_pulp(supplies, demands, distances, mode, config)

    solver = pywraplp.Solver.CreateSolver("SCIP")
    if solver is None:
        return _optimize_multi_resource_pulp(supplies, demands, distances, mode, config)

    resources = list(demands.keys())
    max_distance = config.get("maxDistanceKm", 5_000)
    vehicle_capacity = config.get("vehicleCapacityPerTrip")  # total units per trip (all resources)
    trips_per_depot = config.get("tripsPerDepot")
    budget_limit = config.get("budgetLimit")  # optional cap on transport cost
    time_windows = config.get("timeWindows")  # depot -> (start,end) hours
    cold_chain = set(config.get("coldChainResources", []))
    strict = config.get("modeStrictness", "relaxed") == "strict"

    x: Dict[Tuple[str, str, str], pywraplp.Variable] = {}
    for depot, region_map in distances.items():
        for region in {r for d in demands.values() for r in d.keys()}:
            dist = region_map.get(region, 1e6)
            if dist > max_distance:
                continue
            for res in resources:
                x[(depot, region, res)] = solver.NumVar(0, solver.infinity(), f"x_{depot}_{region}_{res}")

    # Supply constraints per depot/resource
    for depot, stock in supplies.items():
        for res in resources:
            solver.Add(sum(var for (d, _, r), var in x.items() if d == depot and r == res) <= stock.get(res, 0.0))

    # Optional vehicle capacity aggregated across resources per depot
    if vehicle_capacity and trips_per_depot:
        for depot in supplies:
            solver.Add(
                sum(var for (d, _, _), var in x.items() if d == depot) <= vehicle_capacity * trips_per_depot
            )

    # Demand constraints per region/resource with unmet allowance
    unmet = {}
    for res, region_demands in demands.items():
        for region, demand in region_demands.items():
            assigned = sum(var for (d, r, rr), var in x.items() if r == region and rr == res)
            unmet[(region, res)] = solver.NumVar(0, solver.infinity(), f"unmet_{region}_{res}")
            solver.Add(assigned + unmet[(region, res)] >= demand)

    transport_cost = []
    for (depot, region, res), var in x.items():
        dist = distances[depot][region]
        transport_cost.append(dist * var)

    penalty_cost = []
    for (region, res), u in unmet.items():
        penalty = 1200.0 if mode == "regional" else 700.0
        penalty_cost.append(penalty * u)

    objective_terms = transport_cost + penalty_cost
    solver.Minimize(solver.Sum(objective_terms))

    if budget_limit:
        solver.Add(solver.Sum(transport_cost) <= budget_limit)

    status = solver.Solve()
    status_name = _status_name(status)
    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        raise RuntimeError("Allocation problem infeasible")

    shipments = []
    for (depot, region, res), var in x.items():
        qty = var.solution_value()
        if qty <= 0:
            continue
        shipments.append(
            {
                "from": depot,
                "to": region,
                "resource": res,
                "quantity": round(qty, 2),
                "distanceKm": round(distances[depot][region], 2),
            }
        )

    unmet_region_total: Dict[str, float] = {}
    unmet_breakdown: List[Dict[str, Union[str, float]]] = []
    for (region, res), u in unmet.items():
        if u.solution_value() > 0:
            val = round(u.solution_value(), 2)
            unmet_breakdown.append({"region": region, "resource": res, "unmet": val})
            unmet_region_total[region] = unmet_region_total.get(region, 0.0) + val

    # Aggregate unmet per region for downstream prioritization heuristics
    unmet_list = [{"region": r, "unmet": round(val, 2)} for r, val in unmet_region_total.items()]

    return {
        "shipments": shipments,
        "unmet": unmet_list,
        "unmetBreakdown": unmet_breakdown,
        "objective": round(solver.Objective().Value(), 2),
        "mode": mode,
        "solverStatus": status_name,
    }


def _status_name(code: int) -> str:
    if pywraplp is None:
        return "UNKNOWN"
    mapping = {
        pywraplp.Solver.OPTIMAL: "OPTIMAL",
        pywraplp.Solver.FEASIBLE: "FEASIBLE",
        pywraplp.Solver.INFEASIBLE: "INFEASIBLE",
        pywraplp.Solver.UNBOUNDED: "UNBOUNDED",
        pywraplp.Solver.ABNORMAL: "ABNORMAL",
        pywraplp.Solver.NOT_SOLVED: "NOT_SOLVED",
    }
    return mapping.get(code, "UNKNOWN")


def _optimize_single_resource_pulp(
    supplies: Dict[str, float],
    demands: Dict[str, float],
    distances: Dict[str, Dict[str, float]],
    mode: str,
    config: Dict,
) -> Dict:
    if pulp is None:
        raise RuntimeError("Neither OR-Tools nor pulp is available for optimization")
    prob = pulp.LpProblem("allocation_single", pulp.LpMinimize)
    x = {}
    max_dist = config.get("maxDistanceKm", 5_000)
    for depot, regions in distances.items():
        for region in demands:
            dist = regions.get(region, 1e6)
            if dist > max_dist:
                continue
            x[(depot, region)] = pulp.LpVariable(f"x_{depot}_{region}", lowBound=0)

    for depot, supply in supplies.items():
        prob += pulp.lpSum(var for (d, _), var in x.items() if d == depot) <= supply

    unmet = {}
    for region, demand in demands.items():
        assigned = pulp.lpSum(var for (d, r), var in x.items() if r == region)
        unmet[region] = pulp.LpVariable(f"unmet_{region}", lowBound=0)
        prob += assigned + unmet[region] >= demand

    transport = [distances[d][r] * var for (d, r), var in x.items()]
    penalty = []
    for region, u in unmet.items():
        p = 1000.0 if mode == "regional" else 500.0
        penalty.append(p * u)
    prob += pulp.lpSum(transport + penalty)

    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    shipments = []
    for (depot, region), var in x.items():
        qty = var.value()
        if qty and qty > 0:
            shipments.append(
                {
                    "from": depot,
                    "to": region,
                    "quantity": round(qty, 2),
                    "distanceKm": round(distances[depot][region], 2),
                }
            )
    unmet_list = []
    for region, u in unmet.items():
        if u.value() and u.value() > 0:
            unmet_list.append({"region": region, "unmet": round(u.value(), 2)})

    return {
        "shipments": shipments,
        "unmet": unmet_list,
        "objective": round(pulp.value(prob.objective), 2),
        "mode": mode,
        "solverStatus": pulp.LpStatus[prob.status],
    }


def _optimize_multi_resource_pulp(
    supplies: Dict[str, Dict[str, float]],
    demands: Dict[str, Dict[str, float]],
    distances: Dict[str, Dict[str, float]],
    mode: str,
    config: Dict,
) -> Dict:
    if pulp is None:
        raise RuntimeError("Neither OR-Tools nor pulp is available for optimization")

    prob = pulp.LpProblem("allocation_multi", pulp.LpMinimize)
    resources = list(demands.keys())
    max_distance = config.get("maxDistanceKm", 5_000)
    vehicle_capacity = config.get("vehicleCapacityPerTrip")
    trips_per_depot = config.get("tripsPerDepot")
    budget_limit = config.get("budgetLimit")
    cold_chain = set(config.get("coldChainResources", []))
    strict = config.get("modeStrictness", "relaxed") == "strict"
    time_windows = config.get("timeWindows")

    x = {}
    for depot, region_map in distances.items():
        for region in {r for d in demands.values() for r in d.keys()}:
            dist = region_map.get(region, 1e6)
            if dist > max_distance:
                continue
            for res in resources:
                x[(depot, region, res)] = pulp.LpVariable(f"x_{depot}_{region}_{res}", lowBound=0)

    for depot, stock in supplies.items():
        for res in resources:
            prob += pulp.lpSum(var for (d, _, r), var in x.items() if d == depot and r == res) <= stock.get(res, 0.0)

    if vehicle_capacity and trips_per_depot:
        for depot in supplies:
            prob += pulp.lpSum(var for (d, _, _), var in x.items() if d == depot) <= vehicle_capacity * trips_per_depot

    unmet = {}
    for res, region_demands in demands.items():
        for region, demand in region_demands.items():
            assigned = pulp.lpSum(var for (d, r, rr), var in x.items() if r == region and rr == res)
            unmet[(region, res)] = pulp.LpVariable(f"unmet_{region}_{res}", lowBound=0)
            prob += assigned + unmet[(region, res)] >= demand

    transport = []
    for (depot, region, res), var in x.items():
        cost = distances[depot][region] * var
        # cold-chain penalty if resource requires cold chain and no flag set on depot (simplified)
        if res in cold_chain and not config.get("coldChainEnabled", False):
            cost = cost * 1000  # discourage
        transport.append(cost)
    penalty = []
    for (region, res), u in unmet.items():
        p = 1200.0 if mode == "regional" else 700.0
        if strict:
            p *= 2
        penalty.append(p * u)

    prob += pulp.lpSum(transport + penalty)

    if budget_limit:
        prob += pulp.lpSum(transport) <= budget_limit

    prob.solve(pulp.PULP_CBC_CMD(msg=False))

    shipments = []
    for (depot, region, res), var in x.items():
        qty = var.value()
        if qty and qty > 0:
            shipments.append(
                {
                    "from": depot,
                    "to": region,
                    "resource": res,
                    "quantity": round(qty, 2),
                    "distanceKm": round(distances[depot][region], 2),
                }
            )

    unmet_region_total: Dict[str, float] = {}
    unmet_breakdown: List[Dict[str, Union[str, float]]] = []
    for (region, res), u in unmet.items():
        if u.value() and u.value() > 0:
            val = round(u.value(), 2)
            unmet_breakdown.append({"region": region, "resource": res, "unmet": val})
            unmet_region_total[region] = unmet_region_total.get(region, 0.0) + val

    unmet_list = [{"region": r, "unmet": round(val, 2)} for r, val in unmet_region_total.items()]

    return {
        "shipments": shipments,
        "unmet": unmet_list,
        "unmetBreakdown": unmet_breakdown,
        "objective": round(pulp.value(prob.objective), 2),
        "mode": mode,
        "solverStatus": pulp.LpStatus[prob.status],
        "constraints": {
            "budgetLimit": budget_limit,
            "maxDistanceKm": max_distance,
            "vehicleCapacityPerTrip": vehicle_capacity,
            "tripsPerDepot": trips_per_depot,
            "timeWindows": time_windows,
            "coldChainResources": list(cold_chain),
            "modeStrictness": config.get("modeStrictness", "relaxed"),
        },
    }
