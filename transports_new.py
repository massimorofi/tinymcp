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
import asyncio
import threading
import uuid
from typing import Any, Optional, Dict, Tuple
import httpx

# Process and lock management for npx processes (thread-safe)
_npx_processes: Dict[str, subprocess.Popen] = {}
_npx_process_locks: Dict[str, threading.Lock] = {}
_npx_process_lock = threading.Lock()  # Global lock for managing _npx_processes dict


def _get_npx_key(command: str, args: list) -> str:
    """Generate a unique key for an npx process."""
    return f"{command}:{':'.join(args)}"


def _is_process_alive(proc: subprocess.Popen) -> bool:
    """Check if a process is still alive."""
    return proc.poll() is None


def _cleanup_dead_npx_process(key: str) -> None:
    """Remove a dead npx process from the cache."""
    global _npx_processes
    with _npx_process_lock:
        if key in _npx_processes:
            proc = _npx_processes[key]
            # Try to terminate the process gracefully
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception:
                pass
            del _npx_processes[key]
            if key in _npx_process_locks:
                del _npx_process_locks[key]


async def handle_npx_stdio(
    command: str, args: list, data: dict[str, Any]
) -> dict[str, Any]:
    """
    Handle MCP communication via npx/stdio.

    Spawns an npx process (e.g., npx -y package-name) and communicates
    via stdin/stdout. Uses thread locks for concurrency safety.
    First call initializes the process with an initialize message.
    Automatically recovers from dead processes.
    """
    key = _get_npx_key(command, args)

    # Check if process exists and is alive
    with _npx_process_lock:
        proc = _npx_processes.get(key)

    # If process doesn't exist or is dead, create a new one
    if proc is None or proc.poll() is not None:
        # Clean up old dead process if it exists
        if proc is not None:
            _cleanup_dead_npx_process(key)
            print(f"[NPX] Process for {key} died, restarting...")

        # Create new process
        with _npx_process_lock:
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

            print(f"[NPX] Init response: {init_response[:200] if init_response else 'empty'}")

            with _npx_process_lock:
                _npx_processes[key] = proc

    # Check if process died during initialization
    if proc.poll() is not None:
        stderr = proc.stderr.read().decode()
        _cleanup_dead_npx_process(key)
        return {
            "error": {
                "code": -32603,
                "message": f"Process ended unexpectedly. stderr: {stderr[:500]}",
            }
        }

    json_data = json.dumps(data)

    # Send request and read response (thread-safe)
    with _npx_process_locks.get(key, threading.Lock()):
        try:
            proc.stdin.write((json_data + "\n").encode())
            proc.stdin.flush()

            response_line = proc.stdout.readline()
            while response_line and not response_line.strip().startswith(b"{"):
                response_line = proc.stdout.readline()

            if not response_line:
                stderr = proc.stderr.read().decode()
                # Process may have died, clean it up
                _cleanup_dead_npx_process(key)
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
            _cleanup_dead_npx_process(key)
            return {
                "error": {
                    "code": -32603,
                    "message": f"Invalid JSON: {str(e)}, stderr: {stderr[:500]}",
                }
            }
        except Exception as e:
            _cleanup_dead_npx_process(key)
            return {"error": {"code": -32603, "message": str(e)}}


# HTTP session management for streamable-http transport
_http_sessions: Dict[str, Dict[str, Any]] = {}
_http_session_lock = threading.Lock()


def _get_or_create_session(url: str) -> str:
    """Get existing session ID or create a new one for the given URL."""
    with _http_session_lock:
        if url not in _http_sessions:
            _http_sessions[url] = {"session_id": str(uuid.uuid4()), "initialized": False}
        return _http_sessions[url]["session_id"]


async def handle_http_stdio(url: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Handle MCP communication via HTTP (streamable-http transport).

    Forwards JSON-RPC requests to HTTP-based MCP servers
    (SSE or streamable-http transports). Handles SSE-formatted
    responses and session management.
    """
    session_id = _get_or_create_session(url)
    
    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        ) as client:
            # Add session ID header if we have one
            headers = {
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            }
            if _http_sessions[url]["initialized"]:
                headers["MCP-Session-Id"] = session_id
            
            response = await client.post(
                url, 
                json=data,
                headers=headers,
                follow_redirects=False
            )
            
            # Check for session ID in response headers
            response_session_id = response.headers.get("mcp-session-id")
            if response_session_id:
                with _http_session_lock:
                    _http_sessions[url]["session_id"] = response_session_id
            
            # Check if response is SSE-formatted
            content_type = response.headers.get("content-type", "")
            
            if "text/event-stream" in content_type or response.text.startswith("event:"):
                # Parse SSE response
                sse_response = response.text
                # Extract data from SSE format: "event: message\ndata: {...}\n\n"
                if "data: " in sse_response:
                    data_line = sse_response.split("data: ")[1].strip()
                    # Remove trailing newlines
                    data_line = data_line.rstrip('\n')
                    try:
                        return json.loads(data_line)
                    except json.JSONDecodeError:
                        return {
                            "error": {
                                "code": -32603,
                                "message": f"Invalid JSON in SSE response: {data_line[:200]}"
                            }
                        }
                return {
                    "error": {
                        "code": -32603,
                        "message": f"No data found in SSE response"
                    }
                }
            else:
                # Plain JSON response
                response.raise_for_status()
                return response.json()
                
    except httpx.TimeoutException:
        return {"error": {"code": -32603, "message": "HTTP request timed out"}}
    except httpx.HTTPStatusError as e:
        return {"error": {"code": -32603, "message": f"HTTP error: {str(e)}"}}
    except Exception as e:
        return {"error": {"code": -32603, "message": f"HTTP request failed: {str(e)}"}}


# Docker process and lock management for persistent connections
_docker_processes: Dict[str, subprocess.Popen] = {}
_docker_process_locks: Dict[str, threading.Lock] = {}
_docker_process_lock = threading.Lock()


def _get_docker_key(container: str, command: Optional[str] = None, args: Optional[list] = None) -> str:
    """Generate a unique key for a docker process."""
    cmd_str = command or ""
    args_str = ":".join(args) if args else ""
    return f"docker:{container}:{cmd_str}:{args_str}"


def _cleanup_dead_docker_process(key: str) -> None:
    """Remove a dead docker process from the cache."""
    global _docker_processes
    with _docker_process_lock:
        if key in _docker_processes:
            proc = _docker_processes[key]
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception:
                pass
            del _docker_processes[key]
            if key in _docker_process_locks:
                del _docker_process_locks[key]


async def handle_docker_stdio(
    container: str,
    data: dict[str, Any],
    command: Optional[str] = None,
    args: list = None,
) -> dict[str, Any]:
    """
    Handle MCP communication via Docker exec stdio.

    Executes commands inside a running Docker container and
    communicates via stdin/stdout. Uses persistent connections
    for better reliability. Automatically recovers from dead processes.
    """
    key = _get_docker_key(container, command, args)
    json_data = json.dumps(data)

    # Check if persistent process exists and is alive
    with _docker_process_lock:
        proc = _docker_processes.get(key)

    # If process doesn't exist or is dead, create a new one
    if proc is None or proc.poll() is not None:
        # Clean up old dead process if it exists
        if proc is not None:
            _cleanup_dead_docker_process(key)
            print(f"[Docker] Process for {key} died, restarting...")

        # Build docker exec command
        cmd_list = ["docker", "exec", "-i", container]
        if command:
            cmd_list.append(command)
        if args:
            cmd_list.extend(args)

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

        try:
            with _docker_process_lock:
                _docker_process_locks[key] = threading.Lock()

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

                print(f"[Docker] Init response: {init_response[:200] if init_response else 'empty'}")

                with _docker_process_lock:
                    _docker_processes[key] = proc

        except Exception as e:
            return {"error": {"code": -32603, "message": f"Failed to start docker process: {str(e)}"}}

    # Check if process died during initialization
    if proc.poll() is not None:
        stderr = proc.stderr.read().decode()
        _cleanup_dead_docker_process(key)
        return {
            "error": {
                "code": -32603,
                "message": f"Process ended unexpectedly. stderr: {stderr[:500]}",
            }
        }

    # Send request and read response (thread-safe)
    with _docker_process_locks.get(key, threading.Lock()):
        try:
            proc.stdin.write((json_data + "\n").encode())
            proc.stdin.flush()

            # Read response lines looking for JSON-RPC response matching request ID
            request_id = data.get("id")
            response_lines = []
            while True:
                response_line = proc.stdout.readline()
                if not response_line:
                    break
                response_lines.append(response_line)
                line_str = response_line.decode().strip()
                if line_str.startswith("{"):
                    try:
                        parsed = json.loads(line_str)
                        if parsed.get("id") == request_id or "result" in parsed or "error" in parsed:
                            break
                    except json.JSONDecodeError:
                        continue

            if not response_lines:
                stderr = proc.stderr.read().decode()
                _cleanup_dead_docker_process(key)
                return {
                    "error": {
                        "code": -32603,
                        "message": f"No response. stderr: {stderr[:500]}",
                    }
                }

            # Parse the response
            response_text = b"".join(response_lines).decode()
            lines = response_text.strip().split("\n")
            json_response = None

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    if "jsonrpc" in parsed and parsed.get("id") == request_id:
                        json_response = parsed
                        break
                except json.JSONDecodeError:
                    continue

            if json_response:
                return json_response

            # If we couldn't find a matching response, return raw output
            return {
                "status": "executed",
                "container": container,
                "raw_response": response_text[:500],
            }

        except Exception as e:
            _cleanup_dead_docker_process(key)
            return {"error": {"code": -32603, "message": str(e)}}
