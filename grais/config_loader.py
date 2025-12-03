import json
from pathlib import Path
from typing import Any, Dict


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class ConfigNotFound(Exception):
    """Raised when a config file cannot be located."""


def load_config(config_name: str) -> Dict[str, Any]:
    """
    Load a configuration JSON by name.

    Args:
        config_name: Name of the config file without extension.

    Returns:
        Parsed configuration dictionary.
    """
    config_path = CONFIG_DIR / f"{config_name}.json"
    if not config_path.exists():
        raise ConfigNotFound(f"Config file {config_path} not found")
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_mode(config: Dict[str, Any]) -> str:
    mode = config.get("mode")
    if mode not in {"global", "regional"}:
        raise ValueError("Config mode must be 'global' or 'regional'")
    return mode
