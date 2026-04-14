"""Pytest fixtures for MCP Gateway tests."""

import pytest
import json
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI application."""
    yield TestClient(app)


@pytest.fixture(autouse=True)
def reset_config():
    """Reset config to default before each test."""
    with open("config.json", "w") as f:
        json.dump({"mcpServers": {}}, f)

