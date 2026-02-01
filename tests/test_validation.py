"""Unit tests for the validation module."""
import pytest

from grais.validation import (
    ValidationError,
    ConfigSchema,
    ScenarioSchema,
    validate_config_name,
    validate_region_names,
    validate_supplies,
    validate_demands,
    validate_distances,
    validate_predictions,
    validate_optimization_result,
    sanitize_string,
    VALID_MODES,
    VALID_ADMIN_LEVELS,
    VALID_DATA_SOURCES,
    MAX_CONFIG_NAME_LENGTH,
)


class TestValidateConfigName:
    def test_valid_config_name(self):
        """Test valid config names pass validation."""
        assert validate_config_name("global") == "global"
        assert validate_config_name("ontario") == "ontario"
        assert validate_config_name("my-config") == "my-config"
        assert validate_config_name("config_v2") == "config_v2"
        assert validate_config_name("Config.2024") == "Config.2024"

    def test_empty_config_name(self):
        """Test empty config name raises error."""
        with pytest.raises(ValidationError) as exc_info:
            validate_config_name("")
        assert exc_info.value.field == "configName"

    def test_too_long_config_name(self):
        """Test too long config name raises error."""
        long_name = "a" * (MAX_CONFIG_NAME_LENGTH + 1)
        with pytest.raises(ValidationError) as exc_info:
            validate_config_name(long_name)
        assert "too long" in exc_info.value.message.lower()

    def test_path_traversal_prevention(self):
        """Test path traversal attempts are blocked."""
        with pytest.raises(ValidationError):
            validate_config_name("../etc/passwd")
        with pytest.raises(ValidationError):
            validate_config_name("config/../../secret")

    def test_invalid_characters(self):
        """Test invalid characters are rejected."""
        with pytest.raises(ValidationError):
            validate_config_name("config<script>")
        with pytest.raises(ValidationError):
            validate_config_name("config;rm -rf")


class TestValidateRegionNames:
    def test_valid_region_names(self):
        """Test valid region names pass validation."""
        regions = ["Toronto", "Ottawa", "Region-1", "Test_Region"]
        result = validate_region_names(regions)
        assert result == regions

    def test_none_regions(self):
        """Test None input returns None."""
        assert validate_region_names(None) is None

    def test_empty_list(self):
        """Test empty list is valid."""
        assert validate_region_names([]) == []

    def test_too_many_regions(self):
        """Test too many regions raises error."""
        regions = [f"Region-{i}" for i in range(1001)]
        with pytest.raises(ValidationError) as exc_info:
            validate_region_names(regions)
        assert exc_info.value.field == "regions"

    def test_invalid_region_name(self):
        """Test invalid region names are rejected."""
        with pytest.raises(ValidationError):
            validate_region_names(["Valid", "Invalid<script>"])


class TestValidateSupplies:
    def test_valid_single_resource_supplies(self):
        """Test valid single-resource supplies."""
        supplies = {"Depot-1": 1000, "Depot-2": 2000}
        result = validate_supplies(supplies)
        assert result == supplies

    def test_valid_multi_resource_supplies(self):
        """Test valid multi-resource supplies."""
        supplies = {
            "Depot-1": {"food": 500, "water": 800},
            "Depot-2": {"food": 600, "water": 700},
        }
        result = validate_supplies(supplies)
        assert result == supplies

    def test_empty_supplies(self):
        """Test empty supplies raises error."""
        with pytest.raises(ValidationError) as exc_info:
            validate_supplies({})
        assert exc_info.value.field == "supplies"

    def test_negative_quantity(self):
        """Test negative quantities raise error."""
        with pytest.raises(ValidationError):
            validate_supplies({"Depot-1": -100})

    def test_invalid_depot_name(self):
        """Test invalid depot names raise error."""
        with pytest.raises(ValidationError):
            validate_supplies({"<invalid>": 1000})


class TestValidateDemands:
    def test_valid_demands(self):
        """Test valid demands pass validation."""
        demands = {"Region-A": 500, "Region-B": 800}
        result = validate_demands(demands)
        assert result == demands

    def test_empty_demands(self):
        """Test empty demands raises error."""
        with pytest.raises(ValidationError) as exc_info:
            validate_demands({})
        assert exc_info.value.field == "demands"


class TestValidateDistances:
    def test_valid_distances(self):
        """Test valid distance matrix passes validation."""
        distances = {
            "Depot-1": {"Region-A": 10, "Region-B": 20},
            "Depot-2": {"Region-A": 15, "Region-B": 25},
        }
        result = validate_distances(distances)
        assert result == distances

    def test_empty_distances(self):
        """Test empty distances raises error."""
        with pytest.raises(ValidationError) as exc_info:
            validate_distances({})
        assert exc_info.value.field == "distances"

    def test_negative_distance(self):
        """Test negative distances raise error."""
        with pytest.raises(ValidationError):
            validate_distances({"Depot-1": {"Region-A": -10}})


class TestValidatePredictions:
    def test_valid_predictions(self):
        """Test valid predictions pass validation."""
        predictions = [
            {
                "region": "Region-A",
                "shortageProbability": 0.5,
                "shortageSeverity": "medium",
                "expectedShortageDay": 3,
            }
        ]
        result = validate_predictions(predictions)
        assert result == predictions

    def test_empty_predictions(self):
        """Test empty predictions raises error."""
        with pytest.raises(ValidationError) as exc_info:
            validate_predictions([])
        assert exc_info.value.field == "predictions"

    def test_missing_required_fields(self):
        """Test missing required fields raises error."""
        predictions = [{"region": "Region-A"}]  # missing other fields
        with pytest.raises(ValidationError) as exc_info:
            validate_predictions(predictions)
        assert "missing required fields" in exc_info.value.message.lower()

    def test_invalid_probability(self):
        """Test invalid probability raises error."""
        predictions = [
            {
                "region": "Region-A",
                "shortageProbability": 1.5,  # > 1
                "shortageSeverity": "medium",
                "expectedShortageDay": 3,
            }
        ]
        with pytest.raises(ValidationError):
            validate_predictions(predictions)


class TestValidateOptimizationResult:
    def test_valid_optimization_result(self):
        """Test valid optimization result passes."""
        result = {"shipments": [], "unmet": []}
        validated = validate_optimization_result(result)
        assert validated == result

    def test_empty_optimization_result(self):
        """Test empty result raises error."""
        with pytest.raises(ValidationError) as exc_info:
            validate_optimization_result({})
        assert exc_info.value.field == "optimization"


class TestSanitizeString:
    def test_normal_string(self):
        """Test normal string passes through."""
        assert sanitize_string("Hello World") == "Hello World"

    def test_removes_control_characters(self):
        """Test control characters are removed."""
        result = sanitize_string("Hello\x00World")
        assert "\x00" not in result

    def test_max_length(self):
        """Test max length is enforced."""
        long_string = "a" * 500
        result = sanitize_string(long_string, max_length=100)
        assert len(result) == 100


class TestConfigSchema:
    def test_valid_config(self):
        """Test valid config schema."""
        config = ConfigSchema(
            mode="regional",
            region="Ontario",
            adminLevel="city",
            timeHorizonDays=14,
        )
        assert config.mode == "regional"
        assert config.adminLevel == "city"

    def test_invalid_mode(self):
        """Test invalid mode raises error."""
        with pytest.raises(ValueError):
            ConfigSchema(mode="invalid")

    def test_invalid_admin_level(self):
        """Test invalid admin level raises error."""
        with pytest.raises(ValueError):
            ConfigSchema(adminLevel="invalid")

    def test_default_values(self):
        """Test default values are applied."""
        config = ConfigSchema()
        assert config.mode == "global"
        assert config.region == "World"
        assert config.timeHorizonDays == 7


class TestScenarioSchema:
    def test_valid_scenario(self):
        """Test valid scenario schema."""
        scenario = ScenarioSchema(
            depotOutages=["Depot-1"],
            demandSurgeMultiplier=1.5,
            budgetLimit=50000,
        )
        assert scenario.depotOutages == ["Depot-1"]
        assert scenario.demandSurgeMultiplier == 1.5

    def test_surge_multiplier_bounds(self):
        """Test surge multiplier bounds."""
        with pytest.raises(ValueError):
            ScenarioSchema(demandSurgeMultiplier=0.05)  # < 0.1
        with pytest.raises(ValueError):
            ScenarioSchema(demandSurgeMultiplier=15)  # > 10

    def test_empty_scenario(self):
        """Test empty scenario is valid."""
        scenario = ScenarioSchema()
        assert scenario.depotOutages is None
        assert scenario.demandSurgeMultiplier is None
