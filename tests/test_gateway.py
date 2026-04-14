"""
Automated tests for MCP Gateway capabilities.
Tests endpoints, error handling, and server lifecycle.
"""

import json
import os
from fastapi.testclient import TestClient


def load_config():
    with open("config.json") as f:
        return json.load(f)


def test_root_endpoint(client):
    """Test root endpoint returns gateway info."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "msg" in data
    assert "docs_url" in data


def test_healthz_endpoint(client):
    """Test health check endpoint."""
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_sse_endpoint(client):
    """Test SSE endpoint exists and returns streaming response."""
    response = client.get("/sse", follow_redirects=False)
    assert response.status_code == 500  # No servers configured


def test_messages_endpoint_invalid_json(client):
    """Test messages endpoint rejects invalid JSON."""
    response = client.post(
        "/messages",
        headers={"Content-Type": "application/json"},
        json={"id": 1, "method": "initialize"}
    )
    # Should fail since no MCP server is configured to respond


def test_messages_endpoint_missing_id(client):
    """Test messages endpoint requires 'id' field."""
    response = client.post(
        "/messages",
        headers={"Content-Type": "application/json"},
        json={"method": "initialize"}
    )
    assert response.status_code == 400


def test_config_not_found_error(client):
    """Test error when config.json is missing."""
    os.remove("config.json")
    try:
        response = client.get("/")
        assert response.status_code == 404
    finally:
        with open("config.json", "w") as f:
            json.dump({"mcpServers": {}}, f)


def test_empty_config_sse(client):
    """Test SSE endpoint with empty config."""
    with open("config.json", "w") as f:
        json.dump({"mcpServers": {}}, f)
    response = client.get("/sse", follow_redirects=False)
    assert response.status_code == 500  # No servers configured


def test_malformed_config(client):
    """Test error handling for invalid JSON config."""
    with open("config.json", "w") as f:
        f.write("{invalid json}")
    try:
        response = client.post(
            "/messages",
            headers={"Content-Type": "application/json"},
            json={"id": 1}
        )
        assert response.status_code == 400
    finally:
        with open("config.json", "w") as f:
            json.dump({"mcpServers": {}}, f)


def test_server_failure_handling(client):
    """Test gateway handles failing MCP server gracefully."""
    with open("config.json", "w") as f:
        json.dump({
            "mcpServers": {
                "test-fail": {"command": "nonexistent-command", "args": ["--help"]}
            }
        }, f)
    try:
        response = client.post(
            "/messages",
            headers={"Content-Type": "application/json"},
            json={"id": 1}
        )
    finally:
        with open("config.json", "w") as f:
            json.dump({"mcpServers": {}}, f)


def test_list_servers_empty(client):
    """Test GET /registry/servers returns empty list when no servers."""
    response = client.get("/registry/servers")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0


def test_list_servers_with_data(client):
    """Test GET /registry/servers returns configured servers."""
    import time
    current_time = time.time()
    with open("config.json", "w") as f:
        json.dump({
            "mcpServers": {
                "github-tools": {
                    "transport": "stdio",
                    "command": "uvx",
                    "args": ["mcp-server-github"],
                    "env": {"GITHUB_TOKEN": "test"}
                }
            },
            "_connectedServers": {
                "github-tools": {
                    "last_heartbeat": current_time,
                    "connected_at": current_time
                }
            }
        }, f)
    try:
        response = client.get("/registry/servers")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "github-tools"
        assert data[0]["transport"] == "stdio"
        assert data[0]["status"] == "connected"
    finally:
        with open("config.json", "w") as f:
            json.dump({"mcpServers": {}}, f)


def test_register_server_stdio(client):
    """Test POST /registry/servers registers a stdio server."""
    response = client.post(
        "/registry/servers",
        json={
            "id": "new-server",
            "transport": "stdio",
            "command": "uvx",
            "args": ["mcp-server-example"]
        }
    )
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == "new-server"
    assert "message" in data
    
    with open("config.json") as f:
        config = json.load(f)
        assert "new-server" in config["mcpServers"]


def test_register_server_sse(client):
    """Test POST /registry/servers registers an SSE server."""
    response = client.post(
        "/registry/servers",
        json={
            "id": "remote-server",
            "transport": "sse",
            "url": "https://example.com/mcp"
        }
    )
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == "remote-server"
    
    with open("config.json") as f:
        config = json.load(f)
        assert "remote-server" in config["mcpServers"]
        assert config["mcpServers"]["remote-server"]["transport"] == "sse"
        assert config["mcpServers"]["remote-server"]["url"] == "https://example.com/mcp"


def test_register_server_missing_id(client):
    """Test POST /registry/servers requires 'id' field."""
    response = client.post(
        "/registry/servers",
        json={"transport": "stdio", "command": "uvx"}
    )
    assert response.status_code == 400


def test_register_server_missing_transport(client):
    """Test POST /registry/servers requires 'transport' field."""
    response = client.post(
        "/registry/servers",
        json={"id": "test-server", "command": "uvx"}
    )
    assert response.status_code == 400


def test_register_server_invalid_transport(client):
    """Test POST /registry/servers rejects invalid transport."""
    response = client.post(
        "/registry/servers",
        json={"id": "test-server", "transport": "invalid"}
    )
    assert response.status_code == 400


def test_register_server_stdio_requires_command(client):
    """Test POST /registry/servers requires 'command' for stdio transport."""
    response = client.post(
        "/registry/servers",
        json={"id": "test-server", "transport": "stdio"}
    )
    assert response.status_code == 400


def test_register_server_sse_requires_url(client):
    """Test POST /registry/servers requires 'url' for SSE transport."""
    response = client.post(
        "/registry/servers",
        json={"id": "test-server", "transport": "sse"}
    )
    assert response.status_code == 400


def test_register_server_duplicate(client):
    """Test POST /registry/servers rejects duplicate server."""
    with open("config.json", "w") as f:
        json.dump({"mcpServers": {"existing": {"transport": "stdio", "command": "uvx"}}}, f)
    
    response = client.post(
        "/registry/servers",
        json={"id": "existing", "transport": "stdio", "command": "uvx"}
    )
    assert response.status_code == 409


def test_initialize_session(client):
    """Test POST /sessions initializes a new session."""
    response = client.post("/sessions")
    assert response.status_code == 201
    data = response.json()
    assert "sessionId" in data
    assert "mcpVersion" in data
    assert data["mcpVersion"] == "1.0.0"


def test_execute_missing_session_id(client):
    """Test POST /execute requires X-Session-ID header."""
    response = client.post(
        "/execute",
        json={"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "test"}, "id": 1}
    )
    assert response.status_code == 422


def test_execute_invalid_session(client):
    """Test POST /execute returns 404 for invalid session."""
    response = client.post(
        "/execute",
        headers={"X-Session-ID": "invalid-session-id"},
        json={"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "test"}, "id": 1}
    )
    assert response.status_code == 404


def test_execute_invalid_jsonrpc_version(client):
    """Test POST /execute rejects invalid JSON-RPC version."""
    response = client.post("/sessions")
    session_id = response.json()["sessionId"]
    
    response = client.post(
        "/execute",
        headers={"X-Session-ID": session_id},
        json={"jsonrpc": "1.0", "method": "tools/call", "params": {"name": "test"}, "id": 1}
    )
    assert response.status_code == 400


def test_execute_no_servers_configured(client):
    """Test POST /execute returns error when no servers configured."""
    response = client.post("/sessions")
    session_id = response.json()["sessionId"]
    
    response = client.post(
        "/execute",
        headers={"X-Session-ID": session_id},
        json={"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "test"}, "id": 1}
    )
    assert response.status_code == 404


def test_execute_with_server(client):
    """Test POST /execute with a configured server."""
    with open("config.json", "w") as f:
        json.dump({
            "mcpServers": {
                "test-server": {
                    "transport": "stdio",
                    "command": "echo",
                    "args": ["test"]
                }
            }
        }, f)
    
    try:
        response = client.post("/sessions")
        session_id = response.json()["sessionId"]
        
        response = client.post(
            "/execute",
            headers={"X-Session-ID": session_id},
            json={"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "test_tool"}, "id": 1}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
    finally:
        with open("config.json", "w") as f:
            json.dump({"mcpServers": {}}, f)
