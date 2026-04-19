# MCP Gateway Connection Flow Diagrams

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Frontend Browser (React)                      │
│  - fetchServers() → GET /registry/servers                        │
│  - fetchTools() → POST /execute (tools/list)                     │
│  - executeTool() → POST /execute (tools/call)                    │
│  - Session per browser tab (5s auto-refresh)                     │
└──────────────────────┬──────────────────────────────────────────┘
                       │ HTTP
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│              MCP Gateway (FastAPI/Python)                        │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Session Management (/sessions, /execute endpoints)      │  │
│  │ - Validates X-Session-ID header                         │  │
│  │ - Routes requests to appropriate server                 │  │
│  │ - Returns JSON-RPC 2.0 responses                         │  │
│  └──────────────────────────────────────────────────────────┘  │
│                       │                                         │
│  ┌────────────────────┴────────────────┬────────────────────┐  │
│  │ Execution Layer (execution.py)      │                   │   │
│  │ - handle_message()                  │                   │   │
│  │ - Detects transport type            │                   │   │
│  │ - Routes to transport handler       │                   │   │
│  └────────────────────────────────────┘                   │   │
│                       │                                         │
│  ┌────────────────────┴────────────────────────────────────┐  │
│  │ Transport Handlers (transports.py)                      │  │
│  │ - handle_http_stdio()   [FOR SSE SERVERS]             │  │
│  │ - handle_npx_stdio()    [for npm packages]            │  │
│  │ - handle_docker_stdio() [for containers]              │  │
│  └────────────────────────────────────────────────────────┘  │
│                       │                                         │
│  ┌────────────────────┴────────────────────────────────────┐  │
│  │ Registry (registry.py)                                  │  │
│  │ - Loads config.json with server definitions            │  │
│  │ - Tracks connection status & heartbeats                 │  │
│  │ - Discovers Docker MCP servers                          │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┬──────────────┐
        │              │              │              │
        │ HTTP         │ stdio        │ Docker       │
        │ (SSE)        │ (npm/uvx)    │ (containers) │
        ▼              ▼              ▼              │
    ┌────────────┐ ┌────────────┐ ┌────────────┐   │
    │ skills-    │ │ github-    │ │ tinymcp-   │   │
    │ provider   │ │ tools      │ │ desktop-   │   │
    │            │ │            │ │ commander  │   │
    │ :3001/mcp  │ │ (uvx)      │ │ (docker)   │   │
    └────────────┘ └────────────┘ └────────────┘   │
                                                    │
                                                    └─→ Configured in config.json
```

## Skills-Provider Connection Flow (Detailed)

```
1. FRONTEND INITIALIZATION
   ┌─────────────────────────────────────────────┐
   │ App.jsx: useEffect → fetchServers()          │
   │ GET /registry/servers                        │
   └────────────────┬────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────┐
   │ main.py: @app.get("/registry/servers")      │
   │ Calls: list_servers(get_connected_servers())│
   └────────────────┬────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────┐
   │ registry.py: list_servers()                  │
   │ - Loads config.json                          │
   │ - Checks _connectedServers heartbeat        │
   │ - Returns:                                   │
   │   [{                                         │
   │     "id": "skills-provider",                │
   │     "transport": "sse",                      │
   │     "url": "http://localhost:3001/mcp",    │
   │     "status": "connected|disconnected",      │
   │     "last_heartbeat": 1234567890            │
   │   }, ...]                                    │
   └────────────────┬────────────────────────────┘
                    │
                    ▼ (JSON response back to frontend)


2. TOOL DISCOVERY (Per Server)
   ┌─────────────────────────────────────────────┐
   │ App.jsx: toggleServer(serverId)              │
   │ → fetchTools(serverId)                       │
   └────────────────┬────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────┐
   │ Frontend sends:                              │
   │ POST /execute?server=skills-provider         │
   │ Headers: X-Session-ID: <sessionId>          │
   │ Body: {                                      │
   │   "jsonrpc": "2.0",                         │
   │   "method": "tools/list",                   │
   │   "params": {},                             │
   │   "id": 1                                   │
   │ }                                            │
   └────────────────┬────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────┐
   │ main.py: @app.post("/execute")               │
   │ - Validates X-Session-ID header             │
   │ - Extracts server param: "skills-provider"  │
   │ - Calls: handle_message(data, server_name)  │
   └────────────────┬────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────┐
   │ execution.py: handle_message()               │
   │ - Loads config.json                          │
   │ - Finds: registry_config = {                │
   │     "transport": "sse",                      │
   │     "url": "http://localhost:3001/mcp"     │
   │   }                                          │
   │ - Transport type is "sse"                    │
   │ - Calls: handle_http_stdio(url, data)       │
   │ - Updates: _connectedServers["skills-       │
   │            provider"]["last_heartbeat"]     │
   └────────────────┬────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────┐
   │ transports.py: handle_http_stdio()           │
   │ - Creates httpx.AsyncClient                  │
   │ - POST to http://localhost:3001/mcp         │
   │ - Headers: Accept, Content-Type             │
   │ - Timeout: 30 seconds                        │
   │ - Sends tools/list JSON-RPC request         │
   └────────────────┬────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────┐
   │ Skills-Provider Server (External)            │
   │ Receives POST /mcp                           │
   │ Processing: tools/list                       │
   │ Returns: {                                   │
   │   "jsonrpc": "2.0",                         │
   │   "id": 1,                                  │
   │   "result": {                               │
   │     "tools": [                              │
   │       {                                     │
   │         "name": "get_skills",              │
   │         "description": "...",              │
   │         "inputSchema": {...}               │
   │       }, ...                                │
   │     ]                                       │
   │   }                                         │
   │ }                                            │
   └────────────────┬────────────────────────────┘
                    │ (Response back through gateway)
                    ▼
   ┌─────────────────────────────────────────────┐
   │ transports.py: response.json()               │
   │ Returns: {...result object...}              │
   └────────────────┬────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────┐
   │ main.py: /execute endpoint                   │
   │ Wraps response:                              │
   │ {                                            │
   │   "jsonrpc": "2.0",                         │
   │   "id": 1,                                  │
   │   "result": {...tools...}                   │
   │ }                                            │
   └────────────────┬────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────┐
   │ Frontend receives & processes                │
   │ setTools(prev => ({                          │
   │   ...prev,                                   │
   │   [serverId]: toolList                       │
   │ }))                                          │
   │ Renders tools in sidebar                     │
   └─────────────────────────────────────────────┘


3. TOOL EXECUTION
   ┌─────────────────────────────────────────────┐
   │ Frontend: selectTool() → User clicks execute │
   │ executeTool()                                │
   │ POST /execute?server=skills-provider         │
   │ Body: {                                      │
   │   "jsonrpc": "2.0",                         │
   │   "method": "tools/call",                   │
   │   "params": {                               │
   │     "name": "get_skills",                   │
   │     "arguments": {...}                      │
   │   },                                         │
   │   "id": <timestamp>                         │
   │ }                                            │
   └────────────────┬────────────────────────────┘
                    │
                    ▼ [SAME FLOW AS TOOL DISCOVERY]
                    │
                    ▼
   ┌─────────────────────────────────────────────┐
   │ Skills-Provider responds with result        │
   │ Gateway returns to frontend                 │
   │ Frontend stores in results state             │
   │ Display in results panel                    │
   └─────────────────────────────────────────────┘


4. CONNECTION STATUS UPDATES
   ┌─────────────────────────────────────────────┐
   │ Every 5 seconds: Frontend auto-refreshes    │
   │ GET /registry/servers                        │
   │ ↓                                            │
   │ Registry checks:                             │
   │ current_time - last_heartbeat > 30s?        │
   │ ↓                                            │
   │ status = "disconnected" or "connected"      │
   │ ↓                                            │
   │ Frontend updates status indicator (●/○)     │
   └─────────────────────────────────────────────┘


5. ERROR SCENARIOS

   A. Skills-Provider Unreachable
      ┌─────────────────────────────────────────┐
      │ transports.py: handle_http_stdio()       │
      │ POST fails (connection refused/timeout)  │
      └──────────────┬──────────────────────────┘
                     │
                     ▼
      ┌─────────────────────────────────────────┐
      │ Exception caught:                        │
      │ {                                        │
      │   "error": {                             │
      │     "code": -32603,                      │
      │     "message": "HTTP request failed: ..." │
      │   }                                       │
      │ }                                         │
      └──────────────┬──────────────────────────┘
                     │
                     ▼
      ┌─────────────────────────────────────────┐
      │ Frontend receives error                  │
      │ console.error('Failed to fetch tools')   │
      │ No tools displayed                       │
      │ Status remains "disconnected"            │
      └─────────────────────────────────────────┘

   B. Request Timeout (30s)
      ┌─────────────────────────────────────────┐
      │ httpx timeout after 30 seconds           │
      │ Same error handling as above             │
      └─────────────────────────────────────────┘

   C. Invalid JSON RPC Response
      ┌─────────────────────────────────────────┐
      │ response.json() raises JSONDecodeError   │
      │ {                                        │
      │   "error": {                             │
      │     "code": -32603,                      │
      │     "message": "Invalid JSON: ..."       │
      │   }                                       │
      │ }                                         │
      └─────────────────────────────────────────┘
```

## Logging Points

```
Trace Flow (with log prefixes):

Frontend Request
    ↓
[Entry] main.py: POST /execute
    ↓
execution.py: handle_message()
    ↓ [MCP] Error handling message: ...
    ↓
transports.py: handle_http_stdio()
    ↓ [Network timeout/error logging]
    ↓
Skills-Provider (External)
    ↓ (Response or timeout)
    ↓
gateway returns error/result
    ↓
Frontend displays to user
```
