import json
import os
from fastapi import HTTPException

SECRETS_PATH = os.environ.get("SECRETS_PATH", "./secrets.json")


def load_config() -> dict:
    try:
        with open("config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Config file not found")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in config.json: {e}")


def save_config(config: dict) -> None:
    with open("config.json", "w") as f:
        json.dump(config, f, indent=2)


def load_secrets() -> dict[str, str]:
    try:
        with open(SECRETS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Secrets file not found")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in secrets.json: {e}")


def get_default_config() -> dict:
    return {
        "mcpServers": {},
        "_connectedServers": {}
    }
