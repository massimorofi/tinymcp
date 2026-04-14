"""
Transports module - MCP server transport implementations.

Provides different transport handlers for communicating
with MCP servers via:
- npx/stdio (for npm packages)
- HTTP (for SSE/streamable-http)
- Docker stdio (for containerized servers)
"""

import json
import subprocess
from typing import Any, Optional
import httpx

# Process and lock management for npx processes (thread-safe)
_npx_processes = {}
_npx_process_locks = {}


async def handle_npx_stdio(
    command: str, args: list, data: dict[str, Any]
) -> dict[str, Any]:
    """
    Handle MCP communication via npx/stdio.

    Spawns an npx process (e.g., npx -y package-name) and communicates
    via stdin/stdout. Uses thread locks for concurrency safety.
    First call initializes the process with an initialize message.
    """
    import threading

    key = f"{command}:{':'.join(args)}"
    global _npx_processes, _npx_process_locks

    # Initialize process if not already running
    if key not in _npx_processes:
        _npx_process_locks[key] = threading.Lock()

        # Send initialize message to establish MCP protocol
        init_data = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-gateway", "version": "1.0.0"},
                },
                "id": 0,
            }
        )

        cmd_list = [command] + args
        proc = subprocess.Popen(
            cmd_list,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )

        # Send initialize request
        proc.stdin.write((init_data + "\n").encode())
        proc.stdin.flush()

        # Wait for initialize response (skip non-JSON output)
        init_response = proc.stdout.readline()
        while init_response and not init_response.strip().startswith(b"{"):
            init_response = proc.stdout.readline()

        print(
            f"[NPX] Init response: {init_response[:200] if init_response else 'empty'}"
        )
        _npx_processes[key] = proc

    proc = _npx_processes[key]

    # Check if process died
    if proc.poll() is not None:
        return {"error": {"code": -32603, "message": "Process ended unexpectedly"}}

    json_data = json.dumps(data)

    # Send request and read response (thread-safe)
    with _npx_process_locks[key]:
        try:
            proc.stdin.write((json_data + "\n").encode())
            proc.stdin.flush()

            response_line = proc.stdout.readline()
            while response_line and not response_line.strip().startswith(b"{"):
                response_line = proc.stdout.readline()

            if not response_line:
                stderr = proc.stderr.read().decode()
                return {
                    "error": {
                        "code": -32603,
                        "message": f"No response. stderr: {stderr[:500]}",
                    }
                }

            response = json.loads(response_line.decode())
            return response
        except json.JSONDecodeError as e:
            stderr = proc.stderr.read().decode()
            return {
                "error": {
                    "code": -32603,
                    "message": f"Invalid JSON: {str(e)}, stderr: {stderr[:500]}",
                }
            }
        except Exception as e:
            return {"error": {"code": -32603, "message": str(e)}}


async def handle_http_stdio(url: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Handle MCP communication via HTTP.

    Forwards JSON-RPC requests to HTTP-based MCP servers
    (SSE or streamable-http transports).
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": {"code": -32603, "message": f"HTTP request failed: {str(e)}"}}


async def handle_docker_stdio(
    container: str,
    data: dict[str, Any],
    command: Optional[str] = None,
    args: list = None,
) -> dict[str, Any]:
    """
    Handle MCP communication via Docker exec stdio.

    Executes commands inside a running Docker container and
    communicates via stdin/stdout. First sends initialize
    message, then the actual request.
    """
    json_data = json.dumps(data)

    # Initialize MCP protocol
    init_data = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-gateway", "version": "1.0.0"},
            },
            "id": 0,
        }
    )

    combined_input = f"{init_data}\n{json_data}\n"

    # Build docker exec command
    cmd_list = ["docker", "exec", "-i", container]
    if command:
        cmd_list.append(command)
    if args:
        cmd_list.extend(args)

    cmd = " ".join(cmd_list)

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            input=combined_input.encode(),
            capture_output=True,
            timeout=30,
        )

        if result.returncode != 0:
            stderr = result.stderr.decode()
            if stderr:
                return {
                    "error": {
                        "code": -32603,
                        "message": f"Docker exec failed: {stderr}",
                    }
                }

        # Parse response - find JSON-RPC response matching request ID
        response = result.stdout.decode()
        lines = response.strip().split("\n")
        json_response = None

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                if "jsonrpc" in parsed and parsed.get("id") == data.get("id"):
                    json_response = parsed
                    break
            except json.JSONDecodeError:
                continue

        if json_response:
            return json_response

        return {
            "status": "executed",
            "container": container,
            "raw_response": response[:500],
        }
    except subprocess.TimeoutExpired:
        return {"error": {"code": -32603, "message": "Command timed out"}}
    except json.JSONDecodeError as e:
        return {
            "error": {"code": -32603, "message": f"Invalid JSON response: {str(e)}"}
        }
    except Exception as e:
        return {"error": {"code": -32603, "message": str(e)}}
