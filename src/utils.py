"""
src/utils.py

Utility helpers for logging, configuration loading, and file operations.
"""

import os
import json
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, Optional


def setup_logging(
    log_dir: str = "./logs", level: int = logging.INFO
) -> logging.Logger:
    """Configure file + console logging."""
    log_dir = Path(log_dir)
    log_dir.mkdir(exist_ok=True)

    logger = logging.getLogger("vlm_routing")
    logger.setLevel(level)

    if not logger.handlers:
        fh = logging.FileHandler(log_dir / "agent.log")
        fh.setLevel(level)

        ch = logging.StreamHandler()
        ch.setLevel(level)

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)

        logger.addHandler(fh)
        logger.addHandler(ch)

    return logger


def load_config(config_path: str = "config/agent_config.yaml") -> Dict[str, Any]:
    """Load agent configuration from YAML."""
    config_path = Path(config_path)
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_json(data: Any, path: str):
    """Save data to a JSON file, creating parent directories as needed."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_json(path: str) -> Any:
    """Load a JSON file."""
    with open(path, "r") as f:
        return json.load(f)


def ensure_dir(path: str):
    """Ensure the directory for a path exists."""
    Path(path).mkdir(parents=True, exist_ok=True)
