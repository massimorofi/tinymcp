import json
import time
import uuid
from typing import Any, Optional

from config import load_config
from registry import discover_docker_mcp_servers
from transports import handle_npx_stdio, handle_http_stdio, handle_docker_stdio
from fastapi import HTTPException
from mcp.server.sse import SseServerTransport

sessions: dict[str, dict[str, Any]] = {}
_connectedServers: dict[str, dict[str, Any]] = {}


def create_session() -> dict[str, Any]:
    """Create a new session."""
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "id": session_id,
        "mcpVersion": "1.0.0",
        "servers": []
    }
    return {"sessionId": session_id, "mcpVersion": "1.0.0"}


def get_session(session_id: str) -> dict[str, Any]:
    """Get a session by ID."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return sessions[session_id]


async def handle_message(data: dict[str, Any], server_name: Optional[str]) -> dict[str, Any]:
    global _connectedServers
    
    discovered = discover_docker_mcp_servers()
    server_config = discovered.get(server_name, {}) if server_name else {}
    
    config = load_config()
    registry_config = config.get("mcpServers", {}).get(server_name, {})
    
    if server_name:
        current_time = time.time()
        _connectedServers[server_name] = {
            "last_heartbeat": current_time,
            "connected_at": current_time
        }
    
    if registry_config:
        transport_type = registry_config.get("transport", "stdio")
        
        if transport_type == "sse" or transport_type == "streamable-http":
            url = registry_config.get("url")
            if url:
                return await handle_http_stdio(url, data)
        
        identifier = registry_config.get("identifier")
        command = registry_config.get("command")
        args = registry_config.get("args", [])
        if identifier and command:
            return await handle_npx_stdio(command, args, data)
    
    transport_type = server_config.get("transport", "docker-stdio") if server_config else "docker-stdio"
    
    if transport_type == "sse":
        url = server_config.get("url")
        if url:
            return {"status": "forwarded to SSE server", "server": server_name}
    
    if transport_type == "docker-stdio":
        container = server_config.get("container")
        command = server_config.get("command")
        args = server_config.get("args", [])
        if container:
            return await handle_docker_stdio(container, data, command, args)
    
    sse = SseServerTransport("/messages")
    
    try:
        async with sse.connect_sse(None):
            await sse.handle_post_message({"method": "initialize"})
        
        result = await sse.handle_post_message(data)
        return result
    except Exception as e:
        print(f"[MCP] Error handling message: {e}")
        raise


def update_server_heartbeat(server_name: str) -> dict[str, Any]:
    """Update the heartbeat timestamp for a server."""
    global _connectedServers
    
    current_time = time.time()
    _connectedServers[server_name] = {
        "last_heartbeat": current_time,
        "connected_at": current_time
    }
    
    return {"status": "ok", "timestamp": current_time}


def get_connected_servers() -> dict[str, dict[str, Any]]:
    """Get the dictionary of connected servers."""
    return _connectedServers
