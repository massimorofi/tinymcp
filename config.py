"""
Config module - Configuration file management.

Handles loading and saving the gateway configuration
and secrets from JSON files.
"""

import json
import os
from fastapi import HTTPException

# Path to secrets file (can be overridden via SECRETS_PATH env var)
SECRETS_PATH = os.environ.get("SECRETS_PATH", "./secrets.json")


def load_config() -> dict:
    """
    Load configuration from config.json.

    Returns the parsed JSON configuration or raises
    appropriate HTTP exceptions for missing/invalid files.
    """
    try:
        with open("config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Config file not found")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in config.json: {e}")


def save_config(config: dict) -> None:
    """Save configuration to config.json."""
    with open("config.json", "w") as f:
        json.dump(config, f, indent=2)


def load_secrets() -> dict[str, str]:
    """
    Load secrets from secrets.json.

    Used for storing sensitive data like API keys.
    """
    try:
        with open(SECRETS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Secrets file not found")
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid JSON in secrets.json: {e}"
        )


def get_default_config() -> dict:
    """Return the default configuration structure."""
    return {"mcpServers": {}, "_connectedServers": {}}
