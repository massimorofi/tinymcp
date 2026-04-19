"""
Registry module - MCP server discovery and registration management.

Provides functions for:
- Discovering MCP servers from Docker containers
- Managing local server registry
- Fetching/activating/deactivating servers from external MCP Registry
"""

import json
from typing import Any, Optional
import httpx
from fastapi import HTTPException
import subprocess
import time

from config import load_config, save_config

# Official MCP Registry API endpoint
MCP_REGISTRY_URL = "https://registry.modelcontextprotocol.io"


def discover_docker_mcp_servers() -> dict[str, dict[str, Any]]:
    """
    Dynamically discover MCP servers from running Docker containers.

    Scans for containers with names starting with "tinymcp-" (excluding gateway),
    inspects them to determine the runtime (Python/Node), and returns server configurations.
    """
    servers = {}

    try:
        # Get list of running Docker containers
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            for container_name in result.stdout.strip().split("\n"):
                container_name = container_name.strip()
                # Filter for tinymcp-* containers, excluding gateway
                if (
                    container_name.startswith("tinymcp-")
                    and "gateway" not in container_name.lower()
                ):
                    # Get container command and entrypoint to determine runtime
                    inspect_result = subprocess.run(
                        [
                            "docker",
                            "inspect",
                            "--format",
                            "{{.Config.Cmd}}",
                            container_name,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )

                    entrypoint_result = subprocess.run(
                        [
                            "docker",
                            "inspect",
                            "--format",
                            "{{.Config.Entrypoint}}",
                            container_name,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )

                    # Default to Node.js runtime
                    cmd = "/usr/local/bin/node"
                    args = ["index.js"]

                    combined = (
                        inspect_result.stdout.strip()
                        + " "
                        + entrypoint_result.stdout.strip()
                    ).lower()

                    # Detect Python runtime
                    if "python" in combined:
                        cmd = "/usr/local/bin/python3"
                        args = ["server.py"]
                    elif "desktop-commander" in container_name.lower():
                        cmd = "/usr/local/bin/node"
                        args = ["dist/index.js"]

                    servers[container_name] = {
                        "transport": "docker-stdio",
                        "container": container_name,
                        "command": cmd,
                        "args": args,
                    }
    except Exception as e:
        print(f"[Discovery] Error discovering MCP servers: {e}")

    return servers


async def list_servers(
    _connectedServers: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    List all registered MCP servers with their connection status.

    Determines status based on heartbeat timestamp - servers with no heartbeat
    in the last 30 seconds are marked as "disconnected".
    """
    servers = []
    current_time = time.time()

    # Merge runtime _connectedServers with config's version
    try:
        config = load_config()
        config_connected = config.get("_connectedServers", {})
        combined_connected = {**config_connected, **_connectedServers}
    except:
        combined_connected = _connectedServers

    # Process Docker-discovered servers
    discovered = discover_docker_mcp_servers()

    for container_name, cfg in discovered.items():
        if container_name in combined_connected:
            server_state = combined_connected[container_name]
            last_heartbeat = server_state.get("last_heartbeat", 0)
        else:
            last_heartbeat = 0

        # Server is disconnected if no heartbeat in 30 seconds
        time_diff = current_time - last_heartbeat
        status = "disconnected" if time_diff > 30 else "connected"

        server_info = {
            "id": container_name,
            "transport": cfg.get("transport", "stdio"),
            "command": cfg.get("command"),
            "args": cfg.get("args", []),
            "container": cfg.get("container"),
            "status": status,
            "last_heartbeat": last_heartbeat,
        }
        servers.append(server_info)

    # Process config-defined servers
    try:
        config = load_config()
        for server_name, server_config in config.get("mcpServers", {}).items():
            has_identifier = server_config.get("identifier")
            has_command = server_config.get("command")
            has_url = server_config.get("url")
            if has_identifier or has_command or has_url:
                # Check heartbeat to determine status
                if server_name in combined_connected:
                    server_state = combined_connected[server_name]
                    last_heartbeat = server_state.get("last_heartbeat", 0)
                else:
                    last_heartbeat = 0

                time_diff = current_time - last_heartbeat
                status = (
                    "disconnected"
                    if time_diff > 30
                    else "connected"
                    if last_heartbeat > 0
                    else "ready"
                )

                servers.append(
                    {
                        "id": server_name,
                        "transport": server_config.get("transport", "stdio"),
                        "command": server_config.get("command"),
                        "args": server_config.get("args", []),
                        "url": server_config.get("url"),
                        "container": server_config.get("container"),
                        "status": status,
                        "registryType": server_config.get("registryType"),
                        "identifier": server_config.get("identifier"),
                        "last_heartbeat": last_heartbeat,
                    }
                )
    except Exception:
        pass

    return servers


async def register_server(data: dict[str, Any]) -> dict[str, Any]:
    """
    Register a new MCP server in the gateway.

    Validates required fields and transport type, then adds
    the server configuration to config.json.
    """
    required_fields = ["id", "transport"]
    for field in required_fields:
        if field not in data:
            raise HTTPException(
                status_code=400, detail=f"Missing required field: {field}"
            )

    # Validate transport type
    if data["transport"] not in ["stdio", "sse", "streamable-http", "docker-stdio"]:
        raise HTTPException(
            status_code=400,
            detail="Transport must be 'stdio', 'sse', 'streamable-http', or 'docker-stdio'",
        )

    if data["transport"] == "stdio" and not data.get("command"):
        raise HTTPException(
            status_code=400, detail="stdio transport requires 'command' field"
        )

    if data["transport"] == "docker-stdio" and not data.get("container"):
        raise HTTPException(
            status_code=400, detail="docker-stdio transport requires 'container' field"
        )

    if data["transport"] == "sse" and not data.get("url"):
        raise HTTPException(
            status_code=400, detail="sse transport requires 'url' field"
        )

    config = load_config()

    # Check for duplicate server
    if data["id"] in config.get("mcpServers", {}):
        raise HTTPException(
            status_code=409, detail=f"Server '{data['id']}' already exists"
        )

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    # Build server entry with provided fields
    server_entry = {
        "transport": data["transport"],
    }

    if data.get("command"):
        server_entry["command"] = data["command"]
    if data.get("args"):
        server_entry["args"] = data["args"]
    if data.get("url"):
        server_entry["url"] = data["url"]
    if data.get("env"):
        server_entry["env"] = data["env"]
    if data.get("container"):
        server_entry["container"] = data["container"]

    config["mcpServers"][data["id"]] = server_entry
    save_config(config)

    return {"message": "Server registered successfully", "id": data["id"]}


async def list_external_registry_servers(
    limit: int = 100,
    cursor: Optional[str] = None,
    latest_only: bool = False,
    search: Optional[str] = None,
) -> dict[str, Any]:
    """
    Fetch available MCP servers from the official MCP Registry.

    Supports pagination, filtering by latest version, and search.
    Returns normalized server information including transport details.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            params = {"limit": limit}
            if cursor:
                params["cursor"] = cursor
            if latest_only:
                params["version"] = "latest"
            if search:
                params["search"] = search

            # Fetch server list from registry API
            response = await client.get(
                f"{MCP_REGISTRY_URL}/v0.1/servers", params=params
            )
            response.raise_for_status()
            data = response.json()

            servers = []
            for item in data.get("servers", []):
                server_info = item.get("server", {})
                packages = server_info.get("packages", []) or []
                first_package = packages[0] if packages else {}
                transport = first_package.get("transport", {})
                meta = item.get("_meta", {})

                servers.append(
                    {
                        "name": server_info.get("name"),
                        "description": server_info.get("description"),
                        "title": server_info.get("title"),
                        "version": server_info.get("version"),
                        "author": server_info.get("author"),
                        "repository": server_info.get("repository", {}).get("url")
                        if server_info.get("repository")
                        else None,
                        "websiteUrl": server_info.get("websiteUrl"),
                        "registryType": first_package.get("registryType"),
                        "identifier": first_package.get("identifier"),
                        "transport": {
                            "type": transport.get("type"),
                            "url": transport.get("url"),
                        },
                        "runtimeHint": first_package.get("runtimeHint"),
                        "isLatest": meta.get("isLatest", True),
                    }
                )

            next_cursor = data.get("metadata", {}).get("nextCursor")
            return {"servers": servers, "nextCursor": next_cursor}
    except Exception as e:
        print(f"[Registry] Error fetching external servers: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch registry servers: {str(e)}"
        )


async def activate_external_servers(server_names: list[str]) -> dict[str, Any]:
    """
    Activate MCP servers from the external registry.

    Fetches server details from registry and adds them to local config.
    Uses npx/uvx based on registry type (npm/python).
    """
    if not server_names:
        raise HTTPException(status_code=400, detail="No servers specified")

    print(f"[Registry] Activating servers: {server_names}")

    config = load_config()
    activated = []

    for server_name in server_names:
        try:
            encoded_name = server_name.replace("/", "%2F")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{MCP_REGISTRY_URL}/v0.1/servers/{encoded_name}/versions/latest"
                )
                response.raise_for_status()
                server_data = response.json()

                server_info = server_data.get("server", {})
                packages = server_info.get("packages", []) or []
                remotes = server_info.get("remotes", []) or []

                first_package = packages[0] if packages else {}
                first_remote = remotes[0] if remotes else {}
                transport = first_package.get("transport", {}) or first_remote

                registry_type = first_package.get("registryType")
                identifier = first_package.get("identifier")

                server_entry = {
                    "transport": transport.get("type", "stdio")
                    if transport
                    else "stdio",
                    "registryType": registry_type,
                    "identifier": identifier,
                }

                # Configure command based on registry type
                if registry_type == "npm":
                    server_entry["command"] = "npx"
                    server_entry["args"] = ["-y", identifier]
                elif registry_type == "python":
                    server_entry["command"] = "uvx"
                    server_entry["args"] = [identifier]
                elif transport and transport.get("type") in ["sse", "streamable-http"]:
                    server_entry["url"] = transport.get("url")

                config["mcpServers"][server_name] = server_entry
                activated.append(server_name)
                print(f"[Registry] Activated: {server_name} -> {server_entry}")
        except Exception as e:
            print(f"[Registry] Error activating server {server_name}: {e}")
            import traceback

            traceback.print_exc()

    save_config(config)
    print(f"[Registry] Total activated: {len(activated)}")
    return {"activated": activated, "message": f"Activated {len(activated)} servers"}


async def deactivate_external_servers(server_names: list[str]) -> dict[str, Any]:
    """
    Deactivate (remove) MCP servers from local configuration.
    """
    if not server_names:
        raise HTTPException(status_code=400, detail="No servers specified")

    config = load_config()
    deactivated = []

    for server_name in server_names:
        if server_name in config.get("mcpServers", {}):
            del config["mcpServers"][server_name]
            deactivated.append(server_name)

    save_config(config)
    return {
        "deactivated": deactivated,
        "message": f"Deactivated {len(deactivated)} servers",
    }


async def unregister_server(server_name: str) -> dict[str, Any]:
    """Unregister a specific MCP server from the gateway."""
    config = load_config()

    if server_name not in config.get("mcpServers", {}):
        raise HTTPException(status_code=404, detail=f"Server '{server_name}' not found")

    del config["mcpServers"][server_name]
    save_config(config)

    return {"message": f"Server '{server_name}' unregistered"}


async def unregister_all_servers() -> dict[str, Any]:
    """Unregister all MCP servers from the gateway."""
    config = load_config()
    server_count = len(config.get("mcpServers", {}))

    config["mcpServers"] = {}
    save_config(config)

    return {"message": f"Unregistered {server_count} servers"}


async def list_active_external_servers() -> list[dict[str, Any]]:
    """List which external registry servers are currently activated."""
    config = load_config()
    active_servers = []

    for server_name, server_config in config.get("mcpServers", {}).items():
        has_identifier = server_config.get("identifier")
        has_command = server_config.get("command")
        has_url = server_config.get("url")
        if has_identifier or has_command or has_url:
            active_servers.append(
                {
                    "name": server_name,
                    "identifier": server_config.get("identifier"),
                    "registryType": server_config.get("registryType"),
                    "command": server_config.get("command"),
                    "url": server_config.get("url"),
                }
            )

    return active_servers
