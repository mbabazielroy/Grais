"""Comprehensive API endpoint tests for GRAIS."""
import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    """Create a test client for the API."""
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestMetricsEndpoint:
    def test_metrics_returns_expected_keys(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        data = response.json()
        assert "requests" in data
        assert "latencyMs" in data
        assert "solverStatus" in data
        assert "unmetTotal" in data

    def test_admin_metrics_endpoint(self, client):
        response = client.get("/admin/metrics")
        assert response.status_code == 200
        assert "metrics" in response.json()


class TestPredictEndpoint:
    def test_predict_regional_ontario(self, client):
        response = client.post("/predict", json={
            "mode": "regional",
            "configName": "ontario"
        })
        assert response.status_code == 200
        data = response.json()
        assert "predictions" in data
        assert "mode" in data
        assert data["mode"] == "regional"
        assert len(data["predictions"]) > 0

    def test_predict_global(self, client):
        response = client.post("/predict", json={
            "mode": "global",
            "configName": "global"
        })
        assert response.status_code == 200
        data = response.json()
        assert "predictions" in data
        assert data["mode"] == "global"

    def test_predict_with_custom_horizon(self, client):
        response = client.post("/predict", json={
            "mode": "regional",
            "configName": "ontario",
            "horizonDays": 14
        })
        assert response.status_code == 200
        data = response.json()
        assert "predictions" in data

    def test_predict_invalid_config(self, client):
        response = client.post("/predict", json={
            "configName": "nonexistent"
        })
        assert response.status_code == 404


class TestOptimizeEndpoint:
    def test_optimize_regional_ontario(self, client):
        response = client.post("/optimize", json={
            "mode": "regional",
            "configName": "ontario"
        })
        assert response.status_code == 200
        data = response.json()
        assert "allocation" in data
        assert "shipments" in data["allocation"]
        assert "solverStatus" in data["allocation"]

    def test_optimize_global(self, client):
        response = client.post("/optimize", json={
            "mode": "global",
            "configName": "global"
        })
        assert response.status_code == 200
        data = response.json()
        assert "allocation" in data

    def test_optimize_with_custom_data(self, client):
        response = client.post("/optimize", json={
            "supplies": {"Depot-1": 1000, "Depot-2": 2000},
            "demands": {"Region-A": 500, "Region-B": 800},
            "distances": {
                "Depot-1": {"Region-A": 10, "Region-B": 20},
                "Depot-2": {"Region-A": 15, "Region-B": 5}
            },
            "mode": "regional",
            "configName": "ontario"
        })
        assert response.status_code == 200
        data = response.json()
        assert "shipments" in data
        assert "solverStatus" in data

    def test_optimize_with_scenario(self, client):
        response = client.post("/optimize", json={
            "mode": "regional",
            "configName": "ontario",
            "scenario": {"demandSurgeMultiplier": 1.5}
        })
        assert response.status_code == 200
        data = response.json()
        assert "allocation" in data


class TestPrioritizeEndpoint:
    def test_prioritize(self, client):
        # First get predictions and optimization
        pred_resp = client.post("/predict", json={
            "mode": "regional",
            "configName": "ontario"
        })
        opt_resp = client.post("/optimize", json={
            "mode": "regional",
            "configName": "ontario"
        })
        
        response = client.post("/prioritize", json={
            "predictions": pred_resp.json()["predictions"],
            "optimization": opt_resp.json()["allocation"],
            "mode": "regional",
            "configName": "ontario"
        })
        assert response.status_code == 200
        data = response.json()
        assert "priorities" in data
        assert len(data["priorities"]) > 0
        
        # Check priority structure
        priority = data["priorities"][0]
        assert "regionName" in priority
        assert "priorityScore" in priority
        assert "urgencyCategory" in priority


class TestRecommendEndpoint:
    def test_recommend_regional_ontario(self, client):
        response = client.post("/recommend", json={
            "mode": "regional",
            "configName": "ontario"
        })
        assert response.status_code == 200
        data = response.json()
        assert "predictions" in data
        assert "allocation" in data
        assert "priorities" in data
        assert "config" in data

    def test_recommend_global(self, client):
        response = client.post("/recommend", json={
            "mode": "global",
            "configName": "global"
        })
        assert response.status_code == 200
        data = response.json()
        assert "predictions" in data
        assert "allocation" in data

    def test_recommend_with_scenario_depot_outage(self, client):
        response = client.post("/recommend", json={
            "mode": "regional",
            "configName": "ontario",
            "scenario": {"depotOutages": ["Depot-1"]}
        })
        assert response.status_code == 200
        data = response.json()
        assert data["scenario"] == {"depotOutages": ["Depot-1"]}

    def test_recommend_with_scenario_demand_surge(self, client):
        response = client.post("/recommend", json={
            "mode": "regional",
            "configName": "ontario",
            "scenario": {"demandSurgeMultiplier": 1.3}
        })
        assert response.status_code == 200
        data = response.json()
        assert "allocation" in data

    def test_recommend_with_scenario_budget_limit(self, client):
        response = client.post("/recommend", json={
            "mode": "regional",
            "configName": "ontario",
            "scenario": {"budgetLimit": 50000}
        })
        assert response.status_code == 200
        data = response.json()
        assert "warnings" in data

    def test_recommend_with_multi_period(self, client):
        response = client.post("/recommend", json={
            "mode": "regional",
            "configName": "ontario",
            "planningPeriods": 3
        })
        assert response.status_code == 200
        data = response.json()
        assert "allocation" in data


class TestRecommendCompareEndpoint:
    def test_compare_scenarios(self, client):
        response = client.post("/recommend/compare", json={
            "mode": "regional",
            "configName": "ontario",
            "scenarios": [
                {"depotOutages": ["Depot-1"]},
                {"demandSurgeMultiplier": 1.3},
                {"budgetLimit": 40000}
            ]
        })
        assert response.status_code == 200
        data = response.json()
        assert "baseline" in data
        assert "scenarios" in data
        assert "comparison" in data
        assert len(data["scenarios"]) == 3

    def test_compare_single_scenario(self, client):
        response = client.post("/recommend/compare", json={
            "mode": "regional",
            "configName": "ontario",
            "scenarios": [{"depotOutages": ["Depot-1"]}]
        })
        assert response.status_code == 200
        data = response.json()
        assert "baseline" in data
        assert len(data["scenarios"]) == 1


class TestDataEndpoints:
    def test_get_world_regions_data(self, client):
        # Uses config name "world" which loads world_remote_regions.json
        response = client.get("/data/regions/world")
        assert response.status_code == 200
        data = response.json()
        assert "regions" in data

    def test_get_world_depots_data(self, client):
        # Uses config name "world" which loads world_remote_depots.json
        response = client.get("/data/depots/world")
        assert response.status_code == 200
        data = response.json()
        assert "depots" in data

    def test_get_ontario_real_regions(self, client):
        # Uses config name "ontario_real" which loads ontario_real_regions.json
        response = client.get("/data/regions/ontario_real")
        assert response.status_code == 200
        data = response.json()
        assert "regions" in data

    def test_get_ontario_real_depots(self, client):
        # Uses config name "ontario_real" which loads ontario_real_depots.json
        response = client.get("/data/depots/ontario_real")
        assert response.status_code == 200
        data = response.json()
        assert "depots" in data

    def test_get_regions_not_found(self, client):
        response = client.get("/data/regions/nonexistent")
        assert response.status_code == 404

    def test_get_depots_not_found(self, client):
        response = client.get("/data/depots/nonexistent")
        assert response.status_code == 404


class TestMetadataCapabilities:
    def test_metadata_contains_capabilities(self, client):
        response = client.post("/recommend", json={
            "mode": "regional",
            "configName": "ontario"
        })
        assert response.status_code == 200
        data = response.json()
        
        assert "metadata" in data
        metadata = data["metadata"]
        assert "capabilities" in metadata
        assert "sklearn" in metadata["capabilities"]
        assert "ortools" in metadata["capabilities"]
        assert "data_source" in metadata


class TestWorldConfig:
    def test_world_config_predict(self, client):
        response = client.post("/predict", json={
            "mode": "global",
            "configName": "world"
        })
        assert response.status_code == 200

    def test_world_config_recommend(self, client):
        response = client.post("/recommend", json={
            "mode": "global",
            "configName": "world"
        })
        assert response.status_code == 200
