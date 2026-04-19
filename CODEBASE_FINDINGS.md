# MCP Gateway Codebase Analysis - Connection Flow and Architecture

## Overview
This document details how the MCP Gateway connects to MCP servers (specifically skills-provider), registers tools, and exposes them through the frontend UI.

---

## 1. Gateway Connection Logic for Skills-Provider

### Configuration
**Location:** [`config.json`](config.json)

```json
{
  "mcpServers": {
    "skills-provider": {
      "transport": "sse",
      "url": "http://localhost:3001/mcp"
    }
  }
}
```

- **Server ID:** `skills-provider`
- **Transport Type:** `sse` (Server-Sent Events / HTTP-based)
- **URL:** `http://localhost:3001/mcp`

### Connection Flow for SSE Servers

**File:** [`execution.py`](execution.py) - Lines 48-118

When a tool execution request arrives for `skills-provider`:

1. **Message Routing** (lines 48-118):
   - `handle_message()` loads config and discovers the server's transport type
   - For `skills-provider`, transport is `sse`, so it looks for the `url` field
   ```python
   if transport_type == "sse" or transport_type == "streamable-http":
       url = registry_config.get("url")  # http://localhost:3001/mcp
       if url:
           return await handle_http_stdio(url, data)
   ```

2. **HTTP Request Forwarding** ([`transports.py`](transports.py) - Lines 117-137):
   - The gateway forwards JSON-RPC 2.0 requests to the skills-provider URL
   - Uses `httpx.AsyncClient` for HTTP communication
   - Timeout: 30 seconds
   - Headers: `Accept: application/json, text/event-stream`
   
   ```python
   async def handle_http_stdio(url: str, data: dict[str, Any]) -> dict[str, Any]:
       try:
           async with httpx.AsyncClient(
               timeout=30.0,
               headers={
                   "Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json",
               },
           ) as client:
               response = await client.post(url, json=data)
               response.raise_for_status()
               return response.json()
       except Exception as e:
           return {"error": {"code": -32603, "message": f"HTTP request failed: {str(e)}"}}
   ```

3. **Heartbeat & Connection Tracking** ([`execution.py`](execution.py) - Lines 62-70):
   - Gateway tracks server connection state with timestamps
   - Updates heartbeat on every successful message
   ```python
   if server_name:
       current_time = time.time()
       _connectedServers[server_name] = {
           "last_heartbeat": current_time,
           "connected_at": current_time,
       }
   ```

### Connection Status Determination
([`registry.py`](registry.py) - Lines 103-145)

Servers are marked as "disconnected" if no heartbeat received in last 30 seconds:

```python
time_diff = current_time - last_heartbeat
status = "disconnected" if time_diff > 30 else "connected"
```

### Error Handling for Connection Failures

When the gateway cannot reach `skills-provider`:

**Primary Error Points:**

1. **HTTP Timeout/Network Errors** ([`transports.py`](transports.py) - Line 136):
   - JSON-RPC error response: `{"error": {"code": -32603, "message": "HTTP request failed: ..."}}`
   - Includes exception message for debugging

2. **Execution Handler Errors** ([`execution.py`](execution.py) - Line 118):
   - Logs to console: `[MCP] Error handling message: {e}`
   - Returns JSON-RPC error frame to client

3. **Message Endpoint Errors** ([`messages.py`](messages.py) - Lines 18-36):
   - Returns 500 status on internal errors
   - Includes error detail in response

---

## 2. Server Registration and Tool Exposure Mechanism

### Server Registration Endpoints

**File:** [`main.py`](main.py)

#### GET `/registry/servers`
Returns list of all registered MCP servers with connection status:

```python
@app.get("/registry/servers")
async def list_registry_servers():
    connected_servers = get_connected_servers()
    return await list_servers(connected_servers)
```

**Response includes:**
- `id`: Server identifier (e.g., "skills-provider")
- `transport`: Transport type (stdio, sse, docker-stdio)
- `url`: Remote server URL (for SSE)
- `status`: "connected" or "disconnected"
- `last_heartbeat`: Unix timestamp of last heartbeat

#### POST `/registry/servers`
Register a new MCP server:

```python
@app.post("/registry/servers")
async def create_registry_server(request: Request):
    data = await request.json()
    result = await register_server(data)
    return JSONResponse(content=result, status_code=201)
```

**Request Body Format:**
```json
{
  "id": "skills-provider",
  "transport": "sse",
  "url": "http://localhost:3001/mcp"
}
```

### Tool Discovery Flow

**Initiator:** Frontend or Chatto agent
**Files:** [`frontend/src/App.jsx`](frontend/src/App.jsx) + [`agents/chatto/chatto_client.py`](agents/chatto/chatto_client.py)

#### Step 1: Initialize Session
**Endpoint:** `POST /sessions`
**Location:** [`main.py`](main.py) - Lines 151-156

```python
@app.post("/sessions")
async def initialize_session():
    result = create_session()
    return JSONResponse(content=result, status_code=201)
```

**Response:**
```json
{
  "sessionId": "uuid-4",
  "mcpVersion": "1.0.0"
}
```

Session stored in memory: [`execution.py`](execution.py) - Lines 15-28

#### Step 2: Discover All Servers
**Endpoint:** `GET /registry/servers`

Frontend fetches list and stores in state:
```javascript
const fetchServers = async () => {
  const res = await fetch(`${API_BASE}/registry/servers`)
  const data = await res.json()
  setServers(data)
}
```

#### Step 3: Fetch Tools for Each Server
**Endpoint:** `POST /execute?server={serverId}`
**Flow:** [`frontend/src/App.jsx`](frontend/src/App.jsx) - Lines 159-190

Frontend sends JSON-RPC `tools/list` request:
```javascript
const fetchTools = async (serverId) => {
  const res = await fetch(`${API_BASE}/execute?server=${encodeURIComponent(serverId)}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Session-ID': session.sessionId
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      method: 'tools/list',
      params: {},
      id: 1
    })
  })
  const data = await res.json()
  const toolList = data.result?.tools || data.result?.result?.tools || []
  setTools(prev => ({ ...prev, [serverId]: toolList }))
}
```

### Tool Execution
**Endpoint:** `POST /execute?server={serverId}`
**Location:** [`main.py`](main.py) - Lines 158-206

**Flow:**
1. Client sends JSON-RPC `tools/call` request with tool name and arguments
2. Gateway validates session ID (header: `X-Session-ID`)
3. Routes request to appropriate server based on `server` query param
4. Forwards request to server (SSE HTTP POST in this case)
5. Returns JSON-RPC response

**Error Handling:**
- 400: Invalid JSON, missing JSON-RPC version, no server configured
- 404: Session not found, server not configured
- 500: Internal execution error (includes wrapped error details)

---

## 3. Frontend Code Displaying Server Information and Tools

**File:** [`frontend/src/App.jsx`](frontend/src/App.jsx)

### State Management

Key state variables for server/tool display:
```javascript
const [servers, setServers] = useState([])          // List of registered servers
const [tools, setTools] = useState({})              // Map of server -> tools
const [selectedTool, setSelectedTool] = useState(null) // Currently selected tool
const [expandedServers, setExpandedServers] = useState({}) // Which servers are expanded
const [paramValues, setParamValues] = useState({})  // Form input values
const [results, setResults] = useState({})          // Execution results
```

### Server Display (Sidebar)
**Location:** Lines 360-410

```jsx
<aside className="sidebar">
  <div className="server-tree">
    {servers.map(server => (
      <div key={server.id} className="server-item">
        <div className="server-row">
          <span className="status-icon" style={{ color: getStatusColor(server.status) }}>
            {server.status === 'connected' ? '●' : '○'}
          </span>
          <span className="server-name">{server.id}</span>
        </div>
        
        {/* Tools list when expanded */}
        {expandedServers[server.id] && (
          <div className="tools-list">
            {(tools[server.id] || []).map(tool => (
              <div className="tool-row">
                <span className="tool-name">⚡ {tool.name}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    ))}
  </div>
</aside>
```

**Key Features:**
- Status indicator (● = connected, ○ = disconnected)
- Green (#4ade80) for connected, Red (#ef4444) for disconnected
- Tool list loads on server expansion
- Right-click context menu for unregistering

### Tool Selection & Parameter Form
**Location:** Lines 411-480

When a tool is selected:
```jsx
{selectedTool && (
  <div className="params-form">
    <div className="params-fields">
      {Object.entries(selectedTool.inputSchema.properties).map(([paramName, paramConfig]) => (
        <div className="param-field">
          <label>{paramName}</label>
          <input
            type={paramConfig.type === 'integer' ? 'number' : 'text'}
            value={paramValues[paramName] || ''}
            onChange={(e) => handleParamChange(paramName, e.target.value)}
          />
        </div>
      ))}
      <button onClick={executeTool}>Execute</button>
    </div>
  </div>
)}
```

**Parameter Schema Handling:**
- Reads `inputSchema.properties` from tool definition
- Renders appropriate input type (number, text)
- Supports default values
- Type coercion for numbers/integers

### Tool Execution Result Display
**Location:** Lines 411-450

```jsx
const currentResults = results[`${selectedTool.server}:${selectedTool.tool}`] || []

{currentResults.map(result => (
  <div className="result-entry">
    <div className="result-time">{result.timestamp}</div>
    <div className="result-request">Request: {JSON.stringify(result.request)}</div>
    <div className="result-response">Response: {JSON.stringify(result.response, null, 2)}</div>
  </div>
))}
```

### Auto-Refresh
**Location:** Lines 123-127

Frontend refreshes server list every 5 seconds to update connection status:
```javascript
useEffect(() => {
  const interval = setInterval(() => {
    fetchServers()
  }, 5000)
  return () => clearInterval(interval)
}, [])
```

### Registry Management Tab
**Location:** Lines 300+ (Registry Admin section)

- Fetch external MCP Registry servers
- Search and filter servers
- Select/deselect for activation
- Pagination support via cursor

---

## 4. Connection Handling and Error Logging

### Connection Logging Points

#### 1. NPX/Stdio Process Initialization
**File:** [`transports.py`](transports.py) - Line 72
```python
print(f"[NPX] Init response: {init_response[:200] if init_response else 'empty'}")
```

#### 2. Message Execution Errors
**File:** [`execution.py`](execution.py) - Line 118
```python
print(f"[MCP] Error handling message: {e}")
```

#### 3. Docker Server Discovery Errors
**File:** [`registry.py`](registry.py) - Line 101
```python
print(f"[Discovery] Error discovering MCP servers: {e}")
```

#### 4. External Registry API Errors
**File:** [`registry.py`](registry.py) - Line 326
```python
print(f"[Registry] Error fetching external servers: {e}")
```

#### 5. Server Activation
**File:** [`registry.py`](registry.py) - Line 342
```python
print(f"[Registry] Activating servers: {server_names}")
```

#### 6. SSE Connection Lifecycle
**File:** [`messages.py`](messages.py) - Lines 67-69
```python
except asyncio.CancelledError:
    print("[SSE] Connection cancelled by client")
except Exception as e:
    print(f"[SSE] Connection error: {e}")
```

### Error Response Format

All errors follow JSON-RPC 2.0 error format:
```json
{
  "jsonrpc": "2.0",
  "id": <request-id>,
  "error": {
    "code": -32603,
    "message": "<error description>"
  }
}
```

### Connection Timeout Behavior

**HTTP Transports** (skills-provider uses this):
- Timeout: 30 seconds (set in `httpx.AsyncClient`)
- On timeout: Returns error code -32603, message includes timeout info

**NPX/Stdio Processes:**
- No explicit timeout
- Returns error if process dies unexpectedly
- Detects via `proc.poll()` returning non-None

**Docker Exec:**
- Timeout: 30 seconds
- On timeout: Returns error "Command timed out"

### Session Validation

**File:** [`main.py`](main.py) - Lines 160-161
```python
get_session(x_session_id)  # Raises 404 if session not found
```

All `/execute` requests require valid session ID in `X-Session-ID` header.

### Frontend Error Display

**File:** [`frontend/src/App.jsx`](frontend/src/App.jsx)

1. **Failed Tool Fetch:**
   ```javascript
   catch (err) {
     console.error('Failed to fetch tools:', err)
   }
   ```

2. **Failed Tool Execution:**
   ```javascript
   catch (err) {
     setResults(prev => ({
       ...prev,
       [`${selectedTool.server}:${selectedTool.tool}`]: [{ error: err.message }]
     }))
   }
   ```

3. **Failed Server Registration:**
   ```javascript
   catch (err) {
     console.error('Error saving servers:', err)
     alert('Error saving servers: ' + err.message)
   }
   ```

---

## 5. Recent Configuration Details

### Skills-Provider Setup
The skills-provider server is configured as an SSE-based HTTP server:

- **Port:** 3001
- **Endpoint:** `/mcp`
- **Transport:** HTTP POST with JSON-RPC 2.0 protocol
- **Expected Methods:**
  - `tools/list` - Returns available tools
  - `tools/call` - Executes a tool with arguments
  - `initialize` - Protocol initialization (optional for SSE)

### Docker Compose Support
**File:** `compose_mcp_servers_test.yml`

Supports running multiple services including skills-provider in Docker containers.

### Chatto Agent Integration

**File:** [`agents/chatto/chatto_client.py`](agents/chatto/chatto_client.py)

The Chatto agent connects to the gateway to:
1. Discover available tools across all servers
2. Fetch tools with `tools/list` for each server
3. Execute tools with `tools/call`
4. Extract results from JSON-RPC response format

---

## 6. API Reference Summary

| Endpoint | Method | Purpose | Auth |
|----------|--------|---------|------|
| `/` | GET | Health check | None |
| `/healthz` | GET | Simple health check | None |
| `/registry/servers` | GET | List registered servers | None |
| `/registry/servers` | POST | Register new server | None |
| `/registry/servers/{id}` | DELETE | Unregister server | None |
| `/registry/external` | GET | List external registry servers | None |
| `/registry/external/active` | GET | List active external servers | None |
| `/registry/external/activate` | POST | Activate external servers | None |
| `/registry/external/deactivate` | POST | Deactivate external servers | None |
| `/sessions` | POST | Create session | None |
| `/execute` | POST | Execute tool | X-Session-ID |
| `/messages` | POST | Handle messages | None |
| `/sse` | GET | SSE streaming endpoint | None |
| `/heartbeat` | GET/POST | Server heartbeat | server_name param |
| `/mcp-server/initialize` | POST | Server initialization | server_name param |

---

## Key Takeaways

1. **Skills-Provider Connection:** SSE HTTP server at `http://localhost:3001/mcp` configured in `config.json`

2. **Connection Method:** Gateway uses `httpx` async HTTP client to POST JSON-RPC 2.0 requests

3. **Tool Discovery:** Two-step process - fetch servers list, then fetch tools for each server

4. **Error Handling:** Comprehensive error management with timeouts, HTTP exceptions, and JSON-RPC error format

5. **Frontend Display:** Real-time updates every 5 seconds, status indicators, expandable server tree with tools

6. **Session Model:** Clients must create session first, include session ID in all execute requests

7. **Docker Support:** Dynamic discovery of Docker-based MCP servers in addition to configured registry
