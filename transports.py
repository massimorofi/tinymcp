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


def _is_initialized(url: str) -> bool:
    """Check if the session for this URL has been initialized."""
    with _http_session_lock:
        return _http_sessions.get(url, {}).get("initialized", False)


async def _read_sse_response(response: httpx.Response) -> dict[str, Any]:
    """Read a single SSE event from the response stream and return its JSON payload."""
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line is None:
            break
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        elif line.strip() == "":
            if data_lines:
                break

    if not data_lines:
        return {
            "error": {
                "code": -32603,
                "message": "No data found in SSE response",
            }
        }

    data_text = "\n".join(data_lines).strip()
    try:
        return json.loads(data_text)
    except json.JSONDecodeError:
        return {
            "error": {
                "code": -32603,
                "message": f"Invalid JSON in SSE response: {data_text[:200]}"
            }
        }


async def handle_http_stdio(url: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Handle MCP communication via HTTP (streamable-http transport).

    Forwards JSON-RPC requests to HTTP-based MCP servers
    (SSE or streamable-http transports). Handles SSE-formatted
    responses and session management. Properly handles MCP
    initialization handshake for streamable-http servers.
    """
    session_id = _get_or_create_session(url)

    # MCP initialize message (sent automatically on first request)
    init_message = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mcp-gateway", "version": "1.0.0"},
        },
        "id": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            }

            # Send initialize first if not yet initialized
            if not _is_initialized(url):
                async with client.stream("POST", url, json=init_message, headers=headers) as resp:
                    resp.raise_for_status()
                    resp_session_id = resp.headers.get("mcp-session-id")
                    if resp_session_id:
                        with _http_session_lock:
                            _http_sessions[url]["session_id"] = resp_session_id
                            session_id = resp_session_id
                    _http_sessions[url]["initialized"] = True
                    await resp.aread()

            # Now send the actual request with session ID
            headers["MCP-Session-Id"] = session_id
            async with client.stream("POST", url, json=data, headers=headers, follow_redirects=False) as response:
                response_session_id = response.headers.get("mcp-session-id")
                if response_session_id:
                    with _http_session_lock:
                        _http_sessions[url]["session_id"] = response_session_id

                content_type = response.headers.get("content-type", "")
                if "text/event-stream" in content_type:
                    return await _read_sse_response(response)

                body = await response.aread()
                if not body:
                    return {
                        "error": {"code": -32603, "message": "Empty HTTP response"}
                    }
                try:
                    return json.loads(body.decode())
                except json.JSONDecodeError:
                    return {
                        "error": {
                            "code": -32603,
                            "message": f"Invalid JSON response: {body[:200].decode(errors='replace')}"
                        }
                    }

    except httpx.TimeoutException:
        return {"error": {"code": -32603, "message": "HTTP request timed out"}}
    except httpx.HTTPStatusError as e:
        return {"error": {"code": -32603, "message": f"HTTP error: {str(e)}"}}
    except Exception as e:
        return {"error": {"code": -32603, "message": f"HTTP request failed: {str(e)}"}}


# Docker one-shot communication - MCP servers in Docker are typically
# one-shot processes that exit after processing their input.
# We use asyncio.create_subprocess_shell for async-safe I/O.


def _build_docker_exec_args(container: str, command: Optional[str] = None, args: Optional[list] = None) -> list:
    """Build a docker exec command as an argument list for create_subprocess_exec."""
    cmd = ["docker", "exec", "-i", container]
    if command:
        cmd.append(command)
    if args:
        cmd.extend(args)
    return cmd


def _parse_docker_output(stdout: str, request_id: Any) -> dict[str, Any]:
    """Parse docker exec stdout, filtering non-JSON log lines, and find the response for request_id."""
    json_response = None
    init_response = None

    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if parsed.get("id") == 0 and "result" in parsed:
                init_response = parsed
            elif "jsonrpc" in parsed and parsed.get("id") == request_id:
                json_response = parsed
                break
        except json.JSONDecodeError:
            continue

    if json_response:
        return json_response

    if init_response is not None:
        return {
            "error": {
                "code": -32603,
                "message": f"MCP server initialized but no response for id={request_id}. stdout: {stdout[:500]}",
            }
        }

    return {
        "error": {
            "code": -32603,
            "message": f"No JSON-RPC response found. stdout: {stdout[:500]}",
        }
    }


async def handle_docker_stdio(
    container: str,
    data: dict[str, Any],
    command: Optional[str] = None,
    args: list = None,
) -> dict[str, Any]:
    """
    Handle MCP communication via Docker exec stdio (one-shot).

    These MCP servers are one-shot processes: they process all stdin
    input and exit. We send both the initialize message and the actual
    request in a single docker exec call, then parse the responses.

    IMPORTANT: We keep stdin open after sending messages to allow the
    server time to process slow tool calls (e.g., HTTP requests to
    Wikipedia). Closing stdin too early causes the MCP stdio transport
    to drop pending responses.
    """
    request_id = data.get("id")
    json_data = json.dumps(data)

    # Build the docker exec command as argument list
    exec_args = _build_docker_exec_args(container, command, args)

    # Initialize message for MCP protocol handshake
    init_message = json.dumps({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mcp-gateway", "version": "1.0.0"},
        },
        "id": 0,
    })

    # Send both messages (initialize + actual request) via stdin
    stdin_input = init_message + "\n" + json_data + "\n"

    try:
        proc = await asyncio.create_subprocess_exec(
            *exec_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Write all input at once but DON'T close stdin via communicate().
        # Closing stdin too early causes the MCP stdio transport to drop
        # pending responses for slow tool calls (e.g., Wikipedia HTTP requests).
        proc.stdin.write(stdin_input.encode())
        await proc.stdin.drain()

        # Read stdout line by line with a timeout, collecting all JSON-RPC responses
        stdout_lines = []
        total_timeout = 30.0
        idle_timeout = 3.0  # Seconds of no data before we assume done
        start_time = asyncio.get_event_loop().time()
        got_init = False
        got_response = False

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > total_timeout:
                proc.kill()
                await proc.wait()
                return {"error": {"code": -32603, "message": "Docker exec timed out after 30s"}}

            remaining = total_timeout - elapsed
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=min(idle_timeout, remaining),
                )
            except asyncio.TimeoutError:
                # No data within idle window
                if got_init and got_response:
                    break
                continue

            if not line:
                # EOF - server closed stdout
                break

            stdout_lines.append(line)

            # Check what we received
            try:
                parsed = json.loads(line.decode())
                if parsed.get("id") == 0 and "result" in parsed:
                    got_init = True
                    print(f"[Docker] Got init response from {container}")
                if "jsonrpc" in parsed and parsed.get("id") == request_id:
                    got_response = True
                    print(f"[Docker] Got response for id={request_id} from {container}")
            except (json.JSONDecodeError, AttributeError):
                pass

            # Only break after idle timeout (not immediately on response match).
            # Slow tool calls (e.g., Wikipedia HTTP requests) take seconds —
            # we must keep stdin open and keep reading until the response arrives.
            if got_response:
                # Response received — wait briefly for it to settle, then break
                print(f"[Docker] Response received, waiting briefly for {container}")
                await asyncio.sleep(0.5)
                break

        # Close stdin to signal the server we're done — triggers flush and exit
        try:
            proc.stdin.close()
        except Exception:
            pass

        # Collect any remaining output after stdin close
        try:
            remaining_out = await asyncio.wait_for(
                proc.stdout.read(), timeout=2.0
            )
            if remaining_out:
                stdout_lines.append(remaining_out)
        except asyncio.TimeoutError:
            pass

        stderr_bytes = await proc.stderr.read()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

        # Decode all collected output
        stdout = b"".join(stdout_lines).decode(errors="replace")

        if proc.returncode != 0:
            stderr = stderr_bytes.decode(errors="replace").strip()
            print(f"[Docker] Non-zero exit ({proc.returncode}) for {container}: {stderr[:200]}")
            return {
                "error": {
                    "code": -32603,
                    "message": f"docker exec failed (exit {proc.returncode}): {stderr[:500]}",
                }
            }

        return _parse_docker_output(stdout, request_id)

    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return {"error": {"code": -32603, "message": "Docker exec timed out after 30s"}}
    except Exception as e:
        return {"error": {"code": -32603, "message": f"Docker exec failed: {str(e)}"}}
