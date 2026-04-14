import time
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from config import load_config
from registry import (
    discover_docker_mcp_servers,
    list_servers,
    register_server,
    list_external_registry_servers,
    activate_external_servers,
    deactivate_external_servers,
    unregister_server,
    unregister_all_servers,
    list_active_external_servers,
)
from execution import (
    create_session,
    get_session,
    handle_message,
    update_server_heartbeat,
    get_connected_servers,
)
from messages import handle_messages_endpoint, handle_sse_endpoint

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def on_startup():
    pass


@app.get("/")
async def root():
    load_config()
    return {"msg": "MCP Gateway is running", "docs_url": "/docs"}


@app.get("/healthz")
async def healthcheck():
    return {"status": "ok"}


# Registry Endpoints
@app.get("/registry/servers")
async def list_registry_servers():
    connected_servers = get_connected_servers()
    return await list_servers(connected_servers)


@app.post("/registry/servers")
async def create_registry_server(request: Request):
    data = await request.json()
    result = await register_server(data)
    return JSONResponse(content=result, status_code=201)


@app.delete("/registry/servers/{server_name}")
async def delete_registry_server(server_name: str):
    result = await unregister_server(server_name)
    return JSONResponse(content=result)


@app.delete("/registry/servers")
async def delete_all_registry_servers():
    result = await unregister_all_servers()
    return JSONResponse(content=result)


@app.get("/registry/external")
async def get_external_servers(
    limit: int = 100,
    cursor: Optional[str] = None,
    latest_only: bool = False,
    search: Optional[str] = None
):
    return await list_external_registry_servers(limit, cursor, latest_only, search)


@app.post("/registry/external/activate")
async def activate_servers(request: Request):
    data = await request.json()
    server_names = data.get("servers", [])
    result = await activate_external_servers(server_names)
    return JSONResponse(content=result)


@app.post("/registry/external/deactivate")
async def deactivate_servers(request: Request):
    data = await request.json()
    server_names = data.get("servers", [])
    result = await deactivate_external_servers(server_names)
    return JSONResponse(content=result)


@app.get("/registry/external/active")
async def get_active_external_servers():
    return await list_active_external_servers()


# Session Endpoints
@app.post("/sessions")
async def initialize_session():
    result = create_session()
    return JSONResponse(content=result, status_code=201)


@app.post("/execute")
async def execute_tool(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
    server: Optional[str] = None
):
    get_session(x_session_id)  # Validate session exists
    
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    
    if data.get("jsonrpc") != "2.0":
        raise HTTPException(status_code=400, detail="Invalid JSON-RPC version")
    
    method = data.get("method", "")
    tool_name = None
    if method == "tools/call" and data.get("params", {}).get("name"):
        tool_name = data["params"]["name"]
    
    config = load_config()
    server_name = server
    
    if not server_name:
        if tool_name:
            prefix = tool_name.split("_")[0]
            for name in config.get("mcpServers", {}):
                if name == prefix:
                    server_name = name
                    break
        if not server_name:
            server_names = list(config.get("mcpServers", {}).keys())
            if server_names:
                server_name = server_names[0]
    
    if not server_name:
        raise HTTPException(status_code=404, detail="No MCP server configured")
    
    try:
        result = await handle_message(data, server_name)
        return {
            "jsonrpc": "2.0",
            "id": data.get("id"),
            "result": result
        }
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": data.get("id"),
            "error": {
                "code": -32603,
                "message": str(e)
            }
        }


# Message Endpoints
@app.post("/messages")
async def messages_endpoint(request: Request):
    return await handle_messages_endpoint(request)


@app.get("/sse")
async def sse_endpoint(request: Request):
    return await handle_sse_endpoint(request)


# Heartbeat Endpoints
@app.get("/heartbeat")
async def heartbeat(server_name: Optional[str] = None):
    """Endpoint for MCP servers to send heartbeat signals"""
    if server_name:
        return update_server_heartbeat(server_name)
    return {"status": "ok", "timestamp": time.time()}


@app.post("/heartbeat")
async def heartbeat_post(server_name: Optional[str] = None):
    """Alternative POST endpoint for heartbeats"""
    if server_name:
        return update_server_heartbeat(server_name)
    return {"status": "ok", "timestamp": time.time()}


@app.post("/mcp-server/initialize")
async def mcp_initialize(request: Request, server_name: Optional[str] = None):
    """Handle MCP server initialization and register heartbeat"""
    from config import save_config
    
    config = load_config()
    
    if server_name and "_connectedServers" not in config:
        config["_connectedServers"] = {}
    
    current_time = time.time()
    if server_name:
        config["_connectedServers"][server_name] = {
            "last_heartbeat": current_time,
            "connected_at": current_time
        }
        save_config(config)
    
    return {"status": "connected", "timestamp": current_time}
