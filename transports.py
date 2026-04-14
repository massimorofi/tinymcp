import json
import subprocess
from typing import Any, Optional
import httpx

_npx_processes = {}
_npx_process_locks = {}


async def handle_npx_stdio(command: str, args: list, data: dict[str, Any]) -> dict[str, Any]:
    import threading
    
    key = f"{command}:{':'.join(args)}"
    global _npx_processes, _npx_process_locks
    
    if key not in _npx_processes:
        _npx_process_locks[key] = threading.Lock()
        
        init_data = json.dumps({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-gateway", "version": "1.0.0"}
            },
            "id": 0
        })
        
        cmd_list = [command] + args
        proc = subprocess.Popen(
            cmd_list,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1
        )
        
        proc.stdin.write((init_data + "\n").encode())
        proc.stdin.flush()
        
        init_response = proc.stdout.readline()
        while init_response and not init_response.strip().startswith(b'{'):
            init_response = proc.stdout.readline()
        
        print(f"[NPX] Init response: {init_response[:200] if init_response else 'empty'}")
        _npx_processes[key] = proc
    
    proc = _npx_processes[key]
    
    if proc.poll() is not None:
        return {"error": {"code": -32603, "message": "Process ended unexpectedly"}}
    
    json_data = json.dumps(data)
    
    with _npx_process_locks[key]:
        try:
            proc.stdin.write((json_data + "\n").encode())
            proc.stdin.flush()
            
            response_line = proc.stdout.readline()
            while response_line and not response_line.strip().startswith(b'{'):
                response_line = proc.stdout.readline()
            
            if not response_line:
                stderr = proc.stderr.read().decode()
                return {"error": {"code": -32603, "message": f"No response. stderr: {stderr[:500]}"}}
            
            response = json.loads(response_line.decode())
            return response
        except json.JSONDecodeError as e:
            stderr = proc.stderr.read().decode()
            return {"error": {"code": -32603, "message": f"Invalid JSON: {str(e)}, stderr: {stderr[:500]}"}}
        except Exception as e:
            return {"error": {"code": -32603, "message": str(e)}}


async def handle_http_stdio(url: str, data: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": {"code": -32603, "message": f"HTTP request failed: {str(e)}"}}


async def handle_docker_stdio(container: str, data: dict[str, Any], command: Optional[str] = None, args: list = None) -> dict[str, Any]:
    json_data = json.dumps(data)
    
    init_data = json.dumps({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mcp-gateway", "version": "1.0.0"}
        },
        "id": 0
    })
    
    combined_input = f"{init_data}\n{json_data}\n"
    
    cmd_list = ["docker", "exec", "-i", container]
    if command:
        cmd_list.append(command)
    if args:
        cmd_list.extend(args)
    
    cmd = ' '.join(cmd_list)
    
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            input=combined_input.encode(),
            capture_output=True,
            timeout=30
        )
        
        if result.returncode != 0:
            stderr = result.stderr.decode()
            if stderr:
                return {
                    "error": {
                        "code": -32603,
                        "message": f"Docker exec failed: {stderr}"
                    }
                }
        
        response = result.stdout.decode()
        lines = response.strip().split('\n')
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
        
        return {"status": "executed", "container": container, "raw_response": response[:500]}
    except subprocess.TimeoutExpired:
        return {"error": {"code": -32603, "message": "Command timed out"}}
    except json.JSONDecodeError as e:
        return {"error": {"code": -32603, "message": f"Invalid JSON response: {str(e)}"}}
    except Exception as e:
        return {"error": {"code": -32603, "message": str(e)}}
