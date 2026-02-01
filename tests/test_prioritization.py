"""Unit tests for the prioritization module."""
import pytest
from unittest.mock import patch, MagicMock
import os

from grais.prioritization import (
    prioritize_regions,
    _heuristic_prioritization,
    SEVERITY_WEIGHTS,
)


@pytest.fixture
def sample_predictions():
    """Sample prediction results."""
    return [
        {
            "region": "Region-A",
            "shortageProbability": 0.8,
            "shortageSeverity": "high",
            "expectedShortageDay": 2,
        },
        {
            "region": "Region-B",
            "shortageProbability": 0.4,
            "shortageSeverity": "medium",
            "expectedShortageDay": 5,
        },
        {
            "region": "Region-C",
            "shortageProbability": 0.2,
            "shortageSeverity": "low",
            "expectedShortageDay": 7,
        },
    ]


@pytest.fixture
def sample_optimization():
    """Sample optimization results."""
    return {
        "shipments": [
            {"from": "Depot-1", "to": "Region-A", "quantity": 500},
            {"from": "Depot-1", "to": "Region-B", "quantity": 300},
        ],
        "unmet": [
            {"region": "Region-A", "unmet": 100},
            {"region": "Region-C", "unmet": 50},
        ],
    }


@pytest.fixture
def sample_config():
    """Sample configuration."""
    return {
        "mode": "regional",
        "timeHorizonDays": 7,
    }


class TestHeuristicPrioritization:
    def test_output_structure(self, sample_predictions, sample_optimization, sample_config):
        """Test heuristic prioritization output structure."""
        results = _heuristic_prioritization(
            sample_predictions, sample_optimization, "regional", sample_config
        )
        
        assert len(results) == len(sample_predictions)
        
        for result in results:
            assert "regionName" in result
            assert "priorityScore" in result
            assert "urgencyCategory" in result
            assert "recommendedAction" in result
            assert "explanation" in result

    def test_priority_score_bounds(self, sample_predictions, sample_optimization, sample_config):
        """Test priority scores are within bounds."""
        results = _heuristic_prioritization(
            sample_predictions, sample_optimization, "regional", sample_config
        )
        
        for result in results:
            assert 0 <= result["priorityScore"] <= 1

    def test_sorted_by_priority(self, sample_predictions, sample_optimization, sample_config):
        """Test results are sorted by priority score descending."""
        results = _heuristic_prioritization(
            sample_predictions, sample_optimization, "regional", sample_config
        )
        
        scores = [r["priorityScore"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_high_probability_high_priority(self, sample_optimization, sample_config):
        """Test that high probability leads to high priority."""
        predictions = [
            {
                "region": "High-Risk",
                "shortageProbability": 0.95,
                "shortageSeverity": "high",
                "expectedShortageDay": 1,
            },
            {
                "region": "Low-Risk",
                "shortageProbability": 0.1,
                "shortageSeverity": "low",
                "expectedShortageDay": 7,
            },
        ]
        
        results = _heuristic_prioritization(
            predictions, sample_optimization, "regional", sample_config
        )
        
        high_risk_result = next(r for r in results if r["regionName"] == "High-Risk")
        low_risk_result = next(r for r in results if r["regionName"] == "Low-Risk")
        
        assert high_risk_result["priorityScore"] > low_risk_result["priorityScore"]

    def test_urgency_categories(self, sample_optimization, sample_config):
        """Test urgency category assignment."""
        predictions = [
            {"region": "Critical", "shortageProbability": 0.9, "shortageSeverity": "high", "expectedShortageDay": 0},
            {"region": "High", "shortageProbability": 0.7, "shortageSeverity": "high", "expectedShortageDay": 2},
            {"region": "Medium", "shortageProbability": 0.5, "shortageSeverity": "medium", "expectedShortageDay": 4},
            {"region": "Low", "shortageProbability": 0.1, "shortageSeverity": "low", "expectedShortageDay": 7},
        ]
        
        results = _heuristic_prioritization(
            predictions, sample_optimization, "regional", sample_config
        )
        
        # Find results by region
        result_map = {r["regionName"]: r for r in results}
        
        # Higher risk should have higher urgency categories
        assert result_map["Critical"]["urgencyCategory"] in ["critical", "high"]
        assert result_map["Low"]["urgencyCategory"] in ["low", "medium"]

    def test_unmet_demand_penalty(self, sample_config):
        """Test that unmet demand affects priority."""
        predictions = [
            {"region": "Unmet", "shortageProbability": 0.5, "shortageSeverity": "medium", "expectedShortageDay": 4},
            {"region": "Met", "shortageProbability": 0.5, "shortageSeverity": "medium", "expectedShortageDay": 4},
        ]
        
        optimization_with_unmet = {
            "shipments": [],
            "unmet": [{"region": "Unmet", "unmet": 500}],
        }
        
        results = _heuristic_prioritization(
            predictions, optimization_with_unmet, "regional", sample_config
        )
        
        result_map = {r["regionName"]: r for r in results}
        
        # Unmet region should have lower score due to penalty
        assert result_map["Unmet"]["priorityScore"] < result_map["Met"]["priorityScore"]

    def test_allocation_bonus(self, sample_config):
        """Test that allocation provides a bonus."""
        predictions = [
            {"region": "Allocated", "shortageProbability": 0.5, "shortageSeverity": "medium", "expectedShortageDay": 4},
            {"region": "NotAllocated", "shortageProbability": 0.5, "shortageSeverity": "medium", "expectedShortageDay": 4},
        ]
        
        optimization = {
            "shipments": [{"from": "Depot-1", "to": "Allocated", "quantity": 10000}],
            "unmet": [],
        }
        
        results = _heuristic_prioritization(
            predictions, optimization, "regional", sample_config
        )
        
        result_map = {r["regionName"]: r for r in results}
        
        # Allocated region should have higher score due to bonus
        assert result_map["Allocated"]["priorityScore"] > result_map["NotAllocated"]["priorityScore"]

    def test_recommended_action_high_urgency(self, sample_optimization, sample_config):
        """Test recommended action for high urgency."""
        predictions = [
            {"region": "Urgent", "shortageProbability": 0.95, "shortageSeverity": "high", "expectedShortageDay": 0},
        ]
        
        results = _heuristic_prioritization(
            predictions, sample_optimization, "regional", sample_config
        )
        
        assert "expedite" in results[0]["recommendedAction"].lower()

    def test_recommended_action_low_urgency(self, sample_optimization, sample_config):
        """Test recommended action for low urgency."""
        predictions = [
            {"region": "NonUrgent", "shortageProbability": 0.1, "shortageSeverity": "low", "expectedShortageDay": 7},
        ]
        
        results = _heuristic_prioritization(
            predictions, sample_optimization, "regional", sample_config
        )
        
        assert "monitor" in results[0]["recommendedAction"].lower()


class TestPrioritizeRegions:
    def test_local_provider(self, sample_predictions, sample_optimization, sample_config):
        """Test local provider uses heuristics."""
        with patch.dict(os.environ, {"LLM_PROVIDER": "local"}):
            results = prioritize_regions(
                sample_predictions, sample_optimization, "regional", sample_config
            )
            
            assert len(results) == len(sample_predictions)

    def test_fallback_to_heuristics(self, sample_predictions, sample_optimization, sample_config):
        """Test fallback to heuristics when no API key."""
        with patch.dict(os.environ, {"LLM_PROVIDER": "", "OPENAI_API_KEY": ""}, clear=True):
            results = prioritize_regions(
                sample_predictions, sample_optimization, "regional", sample_config
            )
            
            assert len(results) == len(sample_predictions)

    def test_explanation_contains_details(self, sample_predictions, sample_optimization, sample_config):
        """Test explanations contain relevant details."""
        results = prioritize_regions(
            sample_predictions, sample_optimization, "regional", sample_config
        )
        
        for result in results:
            explanation = result["explanation"]
            assert "Probability" in explanation or "probability" in explanation.lower()


class TestSeverityWeights:
    def test_severity_weights_exist(self):
        """Test severity weights are defined."""
        assert "high" in SEVERITY_WEIGHTS
        assert "medium" in SEVERITY_WEIGHTS
        assert "low" in SEVERITY_WEIGHTS

    def test_severity_weights_ordered(self):
        """Test severity weights are properly ordered."""
        assert SEVERITY_WEIGHTS["high"] > SEVERITY_WEIGHTS["medium"]
        assert SEVERITY_WEIGHTS["medium"] > SEVERITY_WEIGHTS["low"]
