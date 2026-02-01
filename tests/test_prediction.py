"""Unit tests for the prediction module."""
import pytest
from unittest.mock import patch, MagicMock
import numpy as np

from grais.prediction import ShortagePredictor, predict_shortages, get_predictor
from grais.data_generator import RegionData


@pytest.fixture
def sample_region():
    """Create a sample region for testing."""
    return RegionData(
        name="TestRegion",
        population=500000,
        current_supply={"food": 1000, "water": 2000, "medicine": 500, "shelter": 300},
        consumption_rate={"food": 100, "water": 200, "medicine": 50, "shelter": 30},
        risk_factor=0.5,
        coordinates=(45.0, -75.0),
    )


@pytest.fixture
def sample_config():
    """Create a sample config for testing."""
    return {
        "mode": "regional",
        "adminLevel": "city",
        "timeHorizonDays": 7,
        "defaultResourceTypes": ["food", "water", "medicine", "shelter"],
    }


class TestShortagePredictor:
    def test_predictor_initialization(self):
        """Test predictor initializes correctly."""
        predictor = ShortagePredictor(random_state=42)
        assert predictor.trained is True
        assert predictor.random_state == 42

    def test_predictor_sklearn_available(self):
        """Test predictor detects sklearn availability."""
        predictor = ShortagePredictor()
        # sklearn should be available in test environment
        assert predictor.has_sklearn is True

    def test_build_features(self, sample_region):
        """Test feature extraction from region."""
        features = ShortagePredictor._build_features(sample_region)
        assert len(features) == 4
        assert features[0] == sample_region.population / 1e6  # population in millions
        assert features[3] == sample_region.risk_factor

    def test_severity_high(self):
        """Test severity calculation for high risk."""
        severity = ShortagePredictor._severity(prob=0.9, expected_day=1, horizon=7)
        assert severity == "high"

    def test_severity_medium(self):
        """Test severity calculation for medium risk."""
        severity = ShortagePredictor._severity(prob=0.5, expected_day=4, horizon=7)
        assert severity == "medium"

    def test_severity_low(self):
        """Test severity calculation for low risk."""
        severity = ShortagePredictor._severity(prob=0.2, expected_day=6, horizon=7)
        assert severity == "low"

    def test_predict_shortages_output_structure(self, sample_region, sample_config):
        """Test prediction output has correct structure."""
        predictor = ShortagePredictor()
        results = predictor.predict_shortages([sample_region], 7, "regional", sample_config)
        
        assert len(results) == 1
        result = results[0]
        
        # Check required fields
        assert "region" in result
        assert "population" in result
        assert "shortageProbability" in result
        assert "shortageProbLower" in result
        assert "shortageProbUpper" in result
        assert "shortageSeverity" in result
        assert "expectedShortageDay" in result
        assert "expectedDayLower" in result
        assert "expectedDayUpper" in result
        assert "mode" in result
        assert "coordinates" in result
        assert "inputs" in result

    def test_predict_shortages_probability_bounds(self, sample_region, sample_config):
        """Test probability is within valid bounds."""
        predictor = ShortagePredictor()
        results = predictor.predict_shortages([sample_region], 7, "regional", sample_config)
        
        result = results[0]
        assert 0 <= result["shortageProbability"] <= 1
        assert 0 <= result["shortageProbLower"] <= 1
        assert 0 <= result["shortageProbUpper"] <= 1
        assert result["shortageProbLower"] <= result["shortageProbability"]
        assert result["shortageProbability"] <= result["shortageProbUpper"]

    def test_predict_shortages_expected_day_bounds(self, sample_region, sample_config):
        """Test expected day is non-negative."""
        predictor = ShortagePredictor()
        results = predictor.predict_shortages([sample_region], 7, "regional", sample_config)
        
        result = results[0]
        assert result["expectedShortageDay"] >= 0
        assert result["expectedDayLower"] >= 0

    def test_predict_shortages_multiple_regions(self, sample_config):
        """Test prediction works with multiple regions."""
        regions = [
            RegionData(
                name=f"Region{i}",
                population=100000 * (i + 1),
                current_supply={"food": 500, "water": 1000, "medicine": 200, "shelter": 100},
                consumption_rate={"food": 50, "water": 100, "medicine": 20, "shelter": 10},
                risk_factor=0.1 * (i + 1),
                coordinates=(45.0 + i, -75.0 + i),
            )
            for i in range(5)
        ]
        
        predictor = ShortagePredictor()
        results = predictor.predict_shortages(regions, 7, "regional", sample_config)
        
        assert len(results) == 5
        region_names = [r["region"] for r in results]
        assert all(f"Region{i}" in region_names for i in range(5))

    def test_interval_calculation(self):
        """Test confidence interval calculation."""
        predictor = ShortagePredictor()
        
        lower, upper = predictor._interval(0.5)
        assert lower < 0.5
        assert upper > 0.5
        assert 0 <= lower <= 1
        assert 0 <= upper <= 1

    def test_day_interval_calculation(self):
        """Test day interval calculation."""
        predictor = ShortagePredictor()
        
        lower, upper = predictor._day_interval(5.0)
        assert lower < 5.0
        assert upper > 5.0
        assert lower >= 0


class TestPredictShortagesFunction:
    def test_predict_shortages_function(self, sample_region, sample_config):
        """Test the module-level predict_shortages function."""
        results = predict_shortages([sample_region], 7, "regional", sample_config)
        
        assert len(results) == 1
        assert results[0]["region"] == "TestRegion"

    def test_get_predictor_caching(self):
        """Test predictor caching works."""
        # Clear cache first
        get_predictor.cache_clear()
        
        predictor1 = get_predictor()
        predictor2 = get_predictor()
        
        # Should return same instance
        assert predictor1 is predictor2


class TestHeuristicFallback:
    def test_heuristic_prediction(self, sample_region):
        """Test heuristic prediction when sklearn is not available."""
        predictor = ShortagePredictor()
        features = predictor._build_features(sample_region)
        
        prob, expected_day = predictor._heuristic_prediction(sample_region, features, 7)
        
        assert 0 <= prob <= 1
        assert expected_day >= 0
