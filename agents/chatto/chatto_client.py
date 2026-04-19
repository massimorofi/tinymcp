"""
Client module for interacting with the MCP Gateway API.

Handles session management, tool discovery, and tool execution calls
to the external Model Context Protocol (MCP) Gateway.
"""

import os
import requests
from typing import Any, Dict, List, Optional

# Default endpoints - can be overridden via environment variables
MCP_GATEWAY_URL = os.environ.get("MCP_GATEWAY_URL", "http://localhost:8080")


class MCPClient:
    """Manages all network interactions with the MCP Gateway."""

    def __init__(self, mcp_gateway_url: str):
        self.mcp_gateway_url = mcp_gateway_url
        self.session = requests.Session()
        self.session_id: Optional[str] = None

    def init_session(self) -> bool:
        """Initialize a session with the MCP Gateway."""
        try:
            response = self.session.post(f"{self.mcp_gateway_url}/sessions")
            if response.ok:
                data = response.json()
                self.session_id = data["sessionId"]
                return True
        except Exception as e:
            print(f"Error initializing session: {e}")
        return False

    def discover_tools(self) -> Dict[str, List[Any]]:
        """
        Connect to MCP Gateway and discover all available tools.

        Queries the registry to find all MCP servers, then calls
        tools/list on each server to discover their capabilities.
        Returns a dictionary mapping server ID to list of tool definitions.
        """
        if not self.session_id:
            self.init_session()

        servers_tools: Dict[str, List[Any]] = {}
        try:
            # 1. Get all registered servers
            response = self.session.get(f"{self.mcp_gateway_url}/registry/servers")
            if not response.ok:
                return {}

            servers = response.json()

            for server in servers:
                server_id = server.get("id")
                try:
                    # 2. Call tools/list for each server
                    tool_response = self.session.post(
                        f"{self.mcp_gateway_url}/execute?server={server_id}",
                        headers={"X-Session-ID": self.session_id},
                        json={
                            "jsonrpc": "2.0",
                            "method": "tools/list",
                            "params": {},
                            "id": 1,
                        },
                    )
                    if tool_response.ok:
                        data = tool_response.json()
                        tools = (
                            data.get("result", {}).get("result", {}).get("tools", [])
                        )
                        servers_tools[server_id] = tools
                except Exception as e:
                    print(f"Error getting tools for {server_id}: {e}")
                    servers_tools[server_id] = []

            return servers_tools
        except Exception as e:
            print(f"Error discovering tools: {e}")
        return {}

    def get_servers(self) -> List[Dict[str, Any]]:
        """Get list of MCP servers from the registry."""
        try:
            response = self.session.get(f"{self.mcp_gateway_url}/registry/servers")
            if response.ok:
                return response.json()
        except:
            pass
        return []

    def execute_tool(
        self, server_name: str, tool_name: str, arguments: Dict[str, Any]
    ) -> dict[str, Any]:
        """
        Execute an MCP tool via the Gateway.

        Sends a tools/call JSON-RPC request to the specified server
        and returns the tool's response content dictionary.
        """
        if not self.session_id:
            self.init_session()

        try:
            response = self.session.post(
                f"{self.mcp_gateway_url}/execute?server={server_name}",
                headers={
                    "X-Session-ID": self.session_id,
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                    "id": 2,
                },
            )
            if response.ok:
                data = response.json()

                result = data.get("result", {}).get("result", {})
                content = result.get("content", [])

                # Extract text content from MCP response
                if content and isinstance(content, list):
                    text_content = content[0].get("text", "")
                    return {
                        "content": text_content
                    }  # Return structured dict for consistency

                return {"raw_result": str(data)}
            return {"error": response.text}
        except Exception as e:
            return {"error": str(e)}
