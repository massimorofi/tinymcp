"""
Execution module - MCP session and message handling.

Provides functions for:
- Creating/managing MCP sessions
- Routing messages to appropriate servers
- Handling different transport types (HTTP, stdio, Docker)
"""

import json
import time
import uuid
import asyncio
import threading
from typing import Any, Optional

from config import load_config
from registry import discover_docker_mcp_servers
from transports import handle_npx_stdio, handle_http_stdio, handle_docker_stdio, _npx_process_lock, _npx_processes, _docker_process_lock, _docker_processes
from fastapi import HTTPException
from mcp.server.sse import SseServerTransport

# In-memory storage for active sessions and connected servers
sessions: dict[str, dict[str, Any]] = {}
_connectedServers: dict[str, dict[str, Any]] = {}


async def cleanup_dead_processes():
    """Background task to periodically clean up dead processes."""
    while True:
        try:
            await asyncio.sleep(30)  # Check every 30 seconds
            
            # Clean up dead npx processes
            with _npx_process_lock:
                dead_keys = []
                for key, proc in _npx_processes.items():
                    if proc.poll() is not None:
                        dead_keys.append(key)
                
                for key in dead_keys:
                    del _npx_processes[key]
                    if key in _npx_process_locks:
                        del _npx_process_locks[key]
                    print(f"[Cleanup] Removed dead npx process: {key}")
            
            # Clean up dead docker processes
            with _docker_process_lock:
                dead_keys = []
                for key, proc in _docker_processes.items():
                    if proc.poll() is not None:
                        dead_keys.append(key)
                
                for key in dead_keys:
                    del _docker_processes[key]
                    if key in _docker_process_locks:
                        del _docker_process_locks[key]
                    print(f"[Cleanup] Removed dead docker process: {key}")
                    
        except Exception as e:
            print(f"[Cleanup] Error during process cleanup: {e}")


def create_session() -> dict[str, Any]:
    """
    Create a new MCP session.

    Generates a unique session ID and initializes session
    metadata. Returns session details to the client.
    """
    session_id = str(uuid.uuid4())
    sessions[session_id] = {"id": session_id, "mcpVersion": "1.0.0", "servers": []}
    return {"sessionId": session_id, "mcpVersion": "1.0.0"}


def get_session(session_id: str) -> dict[str, Any]:
    """Retrieve a session by ID. Raises 404 if not found."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return sessions[session_id]


async def handle_message(
    data: dict[str, Any], server_name: Optional[str]
) -> dict[str, Any]:
    """
    Route and execute MCP messages to the appropriate server.

    Determines the transport type and server to use, then dispatches
    the message to the correct handler (HTTP, npx/stdio, or Docker).
    Updates server heartbeat on successful execution.
    """
    global _connectedServers

    # Discover Docker-based MCP servers
    discovered = discover_docker_mcp_servers()
    server_config = discovered.get(server_name, {}) if server_name else {}

    config = load_config()
    registry_config = config.get("mcpServers", {}).get(server_name, {})

    # Update server heartbeat
    if server_name:
        current_time = time.time()
        _connectedServers[server_name] = {
            "last_heartbeat": current_time,
            "connected_at": current_time,
        }

    # Handle registry-configured servers first
    if registry_config:
        transport_type = registry_config.get("transport", "stdio")

        # HTTP-based transport (SSE or streamable-http)
        if transport_type == "sse" or transport_type == "streamable-http":
            url = registry_config.get("url")
            if url:
                return await handle_http_stdio(url, data)

        # NPM/Python package-based servers (via npx/uvx)
        identifier = registry_config.get("identifier")
        command = registry_config.get("command")
        args = registry_config.get("args", [])
        if identifier and command:
            return await handle_npx_stdio(command, args, data)

    # Fall back to Docker-discovered servers
    transport_type = (
        server_config.get("transport", "docker-stdio")
        if server_config
        else "docker-stdio"
    )

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

    # Default SSE transport
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
    """
    Update the heartbeat timestamp for a server.

    Called periodically by servers to indicate they're
    still alive and connected.
    """
    global _connectedServers

    current_time = time.time()
    _connectedServers[server_name] = {
        "last_heartbeat": current_time,
        "connected_at": current_time,
    }

    return {"status": "ok", "timestamp": current_time}


def get_connected_servers() -> dict[str, dict[str, Any]]:
    """Get the dictionary of currently connected servers."""
    return _connectedServers
