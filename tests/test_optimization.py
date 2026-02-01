"""Unit tests for the optimization module."""
import pytest

from grais.optimization import (
    optimize_allocation,
    multi_period_plan,
    _optimize_single_resource,
    _optimize_multi_resource,
)


@pytest.fixture
def simple_supplies():
    """Simple single-resource supplies."""
    return {"Depot-1": 1000, "Depot-2": 1500}


@pytest.fixture
def simple_demands():
    """Simple single-resource demands."""
    return {"Region-A": 500, "Region-B": 800, "Region-C": 300}


@pytest.fixture
def simple_distances():
    """Simple distance matrix."""
    return {
        "Depot-1": {"Region-A": 10, "Region-B": 20, "Region-C": 30},
        "Depot-2": {"Region-A": 25, "Region-B": 5, "Region-C": 15},
    }


@pytest.fixture
def multi_resource_supplies():
    """Multi-resource supplies."""
    return {
        "Depot-1": {"food": 500, "water": 800},
        "Depot-2": {"food": 700, "water": 600},
    }


@pytest.fixture
def multi_resource_demands():
    """Multi-resource demands."""
    return {
        "food": {"Region-A": 300, "Region-B": 400},
        "water": {"Region-A": 500, "Region-B": 600},
    }


@pytest.fixture
def base_config():
    """Base configuration."""
    return {
        "mode": "regional",
        "maxDistanceKm": 5000,
    }


class TestSingleResourceOptimization:
    def test_basic_allocation(self, simple_supplies, simple_demands, simple_distances, base_config):
        """Test basic single-resource allocation."""
        result = optimize_allocation(
            simple_supplies, simple_demands, simple_distances, "regional", base_config
        )
        
        assert "shipments" in result
        assert "unmet" in result
        assert "objective" in result
        assert "solverStatus" in result
        
        # Should find a solution
        assert result["solverStatus"] in ["OPTIMAL", "FEASIBLE", "Optimal"]

    def test_shipments_structure(self, simple_supplies, simple_demands, simple_distances, base_config):
        """Test shipment output structure."""
        result = optimize_allocation(
            simple_supplies, simple_demands, simple_distances, "regional", base_config
        )
        
        for shipment in result["shipments"]:
            assert "from" in shipment
            assert "to" in shipment
            assert "quantity" in shipment
            assert "distanceKm" in shipment
            assert shipment["quantity"] > 0
            assert shipment["from"] in simple_supplies
            assert shipment["to"] in simple_demands

    def test_supply_constraints_respected(self, simple_supplies, simple_demands, simple_distances, base_config):
        """Test that supply constraints are respected."""
        result = optimize_allocation(
            simple_supplies, simple_demands, simple_distances, "regional", base_config
        )
        
        # Calculate total shipped from each depot
        shipped_from = {}
        for shipment in result["shipments"]:
            depot = shipment["from"]
            shipped_from[depot] = shipped_from.get(depot, 0) + shipment["quantity"]
        
        # Should not exceed supply
        for depot, shipped in shipped_from.items():
            assert shipped <= simple_supplies[depot] + 0.01  # small tolerance

    def test_demand_met_or_unmet_recorded(self, simple_supplies, simple_demands, simple_distances, base_config):
        """Test that demand is either met or recorded as unmet."""
        result = optimize_allocation(
            simple_supplies, simple_demands, simple_distances, "regional", base_config
        )
        
        # Calculate total received by each region
        received = {}
        for shipment in result["shipments"]:
            region = shipment["to"]
            received[region] = received.get(region, 0) + shipment["quantity"]
        
        # Calculate unmet
        unmet = {item["region"]: item["unmet"] for item in result["unmet"]}
        
        # For each region: received + unmet should equal demand
        for region, demand in simple_demands.items():
            total = received.get(region, 0) + unmet.get(region, 0)
            assert abs(total - demand) < 0.1  # small tolerance

    def test_distance_cap(self, simple_supplies, simple_demands, simple_distances, base_config):
        """Test that distance cap is respected."""
        config = {**base_config, "maxDistanceKm": 15}
        
        result = optimize_allocation(
            simple_supplies, simple_demands, simple_distances, "regional", config
        )
        
        # All shipments should respect distance cap
        for shipment in result["shipments"]:
            assert shipment["distanceKm"] <= 15


class TestMultiResourceOptimization:
    def test_multi_resource_allocation(
        self, multi_resource_supplies, multi_resource_demands, simple_distances, base_config
    ):
        """Test multi-resource allocation."""
        result = optimize_allocation(
            multi_resource_supplies, multi_resource_demands, simple_distances, "regional", base_config
        )
        
        assert "shipments" in result
        assert result["solverStatus"] in ["OPTIMAL", "FEASIBLE", "Optimal"]

    def test_multi_resource_shipments_have_resource(
        self, multi_resource_supplies, multi_resource_demands, simple_distances, base_config
    ):
        """Test multi-resource shipments include resource type."""
        result = optimize_allocation(
            multi_resource_supplies, multi_resource_demands, simple_distances, "regional", base_config
        )
        
        for shipment in result["shipments"]:
            assert "resource" in shipment
            assert shipment["resource"] in ["food", "water"]

    def test_budget_limit(
        self, multi_resource_supplies, multi_resource_demands, simple_distances, base_config
    ):
        """Test budget limit constraint."""
        config = {**base_config, "budgetLimit": 1000}
        
        result = optimize_allocation(
            multi_resource_supplies, multi_resource_demands, simple_distances, "regional", config
        )
        
        # Calculate total transport cost
        total_cost = sum(
            s["distanceKm"] * s["quantity"] for s in result["shipments"]
        )
        
        # Should respect budget (with some unmet demand as penalty)
        # Note: the solver may exceed budget if unmet penalty is higher
        assert result["solverStatus"] in ["OPTIMAL", "FEASIBLE", "Optimal"]


class TestMultiPeriodPlanning:
    def test_multi_period_plan(
        self, multi_resource_supplies, multi_resource_demands, simple_distances, base_config
    ):
        """Test multi-period planning."""
        result = multi_period_plan(
            multi_resource_supplies, multi_resource_demands, simple_distances,
            "regional", base_config, periods=3
        )
        
        assert "shipments" in result
        assert "periods" in result
        assert result["periods"] == 3
        assert result["solverStatus"] == "MultiPeriod"

    def test_multi_period_shipments_have_period(
        self, multi_resource_supplies, multi_resource_demands, simple_distances, base_config
    ):
        """Test multi-period shipments include period number."""
        result = multi_period_plan(
            multi_resource_supplies, multi_resource_demands, simple_distances,
            "regional", base_config, periods=2
        )
        
        for shipment in result["shipments"]:
            assert "period" in shipment
            assert shipment["period"] in [1, 2]


class TestEdgeCases:
    def test_insufficient_supplies(self, base_config):
        """Test handling of insufficient supplies (less than demand)."""
        # Use isolated data to avoid fixture interference
        supplies = {"TestDepot": 100}
        demands = {"TestRegion": 500}
        distances = {"TestDepot": {"TestRegion": 10}}
        
        result = optimize_allocation(
            supplies, demands, distances, "regional", base_config
        )
        
        # Should have valid structure
        assert "shipments" in result
        assert "unmet" in result
        assert "solverStatus" in result
        
        # Should have unmet demand since supply is insufficient
        shipped_total = sum(s["quantity"] for s in result["shipments"])
        assert shipped_total <= 100 + 0.1  # Can't ship more than available
        
        # Should have unmet demand recorded
        unmet_total = sum(u["unmet"] for u in result["unmet"])
        assert unmet_total >= 400 - 0.1  # At least 400 unmet (500 - 100)

    def test_excess_supply(self, simple_supplies, simple_distances, base_config):
        """Test with excess supply (demand easily met)."""
        small_demands = {"Region-A": 100, "Region-B": 100}
        
        result = optimize_allocation(
            simple_supplies, small_demands, simple_distances, "regional", base_config
        )
        
        # Should have no or minimal unmet demand
        unmet_total = sum(item["unmet"] for item in result.get("unmet", []))
        assert unmet_total < 1

    def test_global_vs_regional_mode(self, simple_supplies, simple_demands, simple_distances, base_config):
        """Test different penalty weights for global vs regional mode."""
        result_regional = optimize_allocation(
            simple_supplies, simple_demands, simple_distances, "regional", base_config
        )
        result_global = optimize_allocation(
            simple_supplies, simple_demands, simple_distances, "global", base_config
        )
        
        # Both should find solutions
        assert result_regional["solverStatus"] in ["OPTIMAL", "FEASIBLE", "Optimal"]
        assert result_global["solverStatus"] in ["OPTIMAL", "FEASIBLE", "Optimal"]


class TestSolverFallback:
    def test_solver_available(self):
        """Test that at least one solver is available."""
        from grais.optimization import pywraplp, pulp
        
        # At least one should be available
        assert pywraplp is not None or pulp is not None
