# MCP Gateway - Code Examples and Implementation Guide

## 1. How Skills-Provider Registration Works

### Configuration (config.json)
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

### Loading Configuration
**File:** `config.py`
```python
def load_config() -> dict[str, Any]:
    """Load MCP server configuration from config.json"""
    try:
        with open("config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Config file not found")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in config.json: {e}")
```

### Registering a New Server via API
**File:** `main.py`
```python
@app.post("/registry/servers")
async def create_registry_server(request: Request):
    """Register a new MCP server"""
    data = await request.json()
    result = await register_server(data)
    return JSONResponse(content=result, status_code=201)
```

**Request Example:**
```bash
curl -X POST http://localhost:8000/registry/servers \
  -H "Content-Type: application/json" \
  -d '{
    "id": "skills-provider",
    "transport": "sse",
    "url": "http://localhost:3001/mcp"
  }'
```

### Registration Validation
**File:** `registry.py`
```python
async def register_server(data: dict[str, Any]) -> dict[str, Any]:
    """Register a new MCP server in the gateway."""
    required_fields = ["id", "transport"]
    for field in required_fields:
        if field not in data:
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")

    # Validate transport type
    if data["transport"] not in ["stdio", "sse", "docker-stdio"]:
        raise HTTPException(
            status_code=400,
            detail="Transport must be 'stdio', 'sse', or 'docker-stdio'",
        )

    # SSE-specific validation
    if data["transport"] == "sse" and not data.get("url"):
        raise HTTPException(status_code=400, detail="sse transport requires 'url' field")

    config = load_config()

    # Check for duplicate
    if data["id"] in config.get("mcpServers", {}):
        raise HTTPException(status_code=409, detail=f"Server '{data['id']}' already exists")

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    # Build server entry
    server_entry = {"transport": data["transport"]}
    if data.get("url"):
        server_entry["url"] = data["url"]

    config["mcpServers"][data["id"]] = server_entry
    save_config(config)

    return {"message": "Server registered successfully", "id": data["id"]}
```

---

## 2. Tool Discovery Implementation

### Session Initialization
**File:** `execution.py`
```python
def create_session() -> dict[str, Any]:
    """Create a new MCP session"""
    session_id = str(uuid.uuid4())
    sessions[session_id] = {"id": session_id, "mcpVersion": "1.0.0", "servers": []}
    return {"sessionId": session_id, "mcpVersion": "1.0.0"}

def get_session(session_id: str) -> dict[str, Any]:
    """Retrieve a session by ID"""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return sessions[session_id]
```

### Listing All Servers with Status
**File:** `registry.py`
```python
async def list_servers(
    _connectedServers: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """List all registered MCP servers with connection status"""
    servers = []
    current_time = time.time()

    # Load config and merge heartbeat data
    config = load_config()
    config_connected = config.get("_connectedServers", {})
    combined_connected = {**config_connected, **_connectedServers}

    # Discover Docker servers
    discovered = discover_docker_mcp_servers()

    for container_name, cfg in discovered.items():
        if container_name in combined_connected:
            server_state = combined_connected[container_name]
            last_heartbeat = server_state.get("last_heartbeat", 0)
        else:
            last_heartbeat = 0

        # Status: connected if heartbeat < 30 seconds old
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

    # Also include configured servers from registry
    for server_name, server_config in config.get("mcpServers", {}).items():
        if server_name not in [s["id"] for s in servers]:
            if server_name in combined_connected:
                last_heartbeat = combined_connected[server_name].get("last_heartbeat", 0)
            else:
                last_heartbeat = 0

            time_diff = current_time - last_heartbeat
            status = "disconnected" if time_diff > 30 else "connected"

            server_info = {
                "id": server_name,
                "transport": server_config.get("transport"),
                "url": server_config.get("url"),
                "command": server_config.get("command"),
                "status": status,
                "last_heartbeat": last_heartbeat,
            }
            servers.append(server_info)

    return servers
```

### Tool Discovery Request
**File:** `execution.py`
```python
async def handle_message(
    data: dict[str, Any], server_name: Optional[str]
) -> dict[str, Any]:
    """
    Route and execute MCP messages to appropriate server
    
    Called when:
    - tools/list - to discover tools
    - tools/call - to execute a tool
    """
    global _connectedServers

    config = load_config()
    registry_config = config.get("mcpServers", {}).get(server_name, {})

    # Update server heartbeat to mark as "connected"
    if server_name:
        current_time = time.time()
        _connectedServers[server_name] = {
            "last_heartbeat": current_time,
            "connected_at": current_time,
        }

    # Determine transport type and forward request
    if registry_config:
        transport_type = registry_config.get("transport", "stdio")

        # HTTP-based transport (SSE servers like skills-provider)
        if transport_type == "sse" or transport_type == "streamable-http":
            url = registry_config.get("url")
            if url:
                return await handle_http_stdio(url, data)

        # Other transports...

    raise HTTPException(status_code=404, detail="Server not found or no transport configured")
```

---

## 3. HTTP Transport Handler (Skills-Provider)

### HTTP Forwarding
**File:** `transports.py`
```python
async def handle_http_stdio(url: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Forward JSON-RPC request to HTTP-based MCP server (SSE)
    
    Args:
        url: Remote MCP server URL (e.g., http://localhost:3001/mcp)
        data: JSON-RPC 2.0 request object
    
    Returns:
        JSON-RPC 2.0 response object
    """
    try:
        async with httpx.AsyncClient(
            timeout=30.0,  # 30-second timeout per request
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        ) as client:
            # Forward JSON-RPC request to remote server
            response = await client.post(url, json=data)
            response.raise_for_status()  # Raise on HTTP error status
            return response.json()  # Parse and return JSON response
            
    except httpx.TimeoutException:
        return {
            "error": {
                "code": -32603,
                "message": "HTTP request timed out (30s)"
            }
        }
    except httpx.HTTPError as e:
        return {
            "error": {
                "code": -32603,
                "message": f"HTTP request failed: {str(e)}"
            }
        }
    except Exception as e:
        return {
            "error": {
                "code": -32603,
                "message": f"HTTP request failed: {str(e)}"
            }
        }
```

### Expected Skills-Provider Response Format
When skills-provider receives a request, it should respond with JSON-RPC 2.0 format:

**For tools/list:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "get_skills",
        "description": "Get available skills",
        "inputSchema": {
          "type": "object",
          "properties": {
            "category": {
              "type": "string",
              "description": "Skill category"
            }
          },
          "required": ["category"]
        }
      }
    ]
  }
}
```

**For tools/call:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Result of tool execution"
      }
    ]
  }
}
```

---

## 4. Execute Endpoint Implementation

### Main Execute Handler
**File:** `main.py`
```python
@app.post("/execute")
async def execute_tool(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
    server: Optional[str] = None,
):
    """
    Execute MCP tool call
    
    Args:
        request: FastAPI request
        x_session_id: Session ID header (required)
        server: Target server name (query param)
    
    Returns:
        JSON-RPC 2.0 response with result or error
    """
    # Validate session exists
    get_session(x_session_id)

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Validate JSON-RPC version
    if data.get("jsonrpc") != "2.0":
        raise HTTPException(status_code=400, detail="Invalid JSON-RPC version")

    # Extract method and tool name
    method = data.get("method", "")  # e.g., "tools/call" or "tools/list"
    tool_name = None
    if method == "tools/call" and data.get("params", {}).get("name"):
        tool_name = data["params"]["name"]

    config = load_config()
    server_name = server

    # Auto-detect server from tool name prefix if not specified
    # e.g., "mysql_query" → server "mysql"
    if not server_name:
        if tool_name:
            prefix = tool_name.split("_")[0]
            for name in config.get("mcpServers", {}):
                if name == prefix:
                    server_name = name
                    break
        # Fallback to first server
        if not server_name:
            server_names = list(config.get("mcpServers", {}).keys())
            if server_names:
                server_name = server_names[0]

    if not server_name:
        raise HTTPException(status_code=404, detail="No MCP server configured")

    try:
        # Forward to server and get result
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
            "error": {"code": -32603, "message": str(e)},
        }
```

---

## 5. Frontend Tool Discovery and Execution

### Initialize Session and Load Servers
**File:** `frontend/src/App.jsx`
```javascript
const initSession = async () => {
  try {
    const res = await fetch(`${API_BASE}/sessions`, { method: 'POST' })
    if (!res.ok) throw new Error('Failed to create session')
    const data = await res.json()
    setSession(data)
    console.log('Session created:', data.sessionId)
  } catch (err) {
    setError(err.message)
  }
}

const fetchServers = async () => {
  try {
    const res = await fetch(`${API_BASE}/registry/servers`)
    if (!res.ok) throw new Error('Failed to fetch servers')
    const data = await res.json()
    setServers(data)
    console.log('Servers loaded:', data.length)
  } catch (err) {
    setError(err.message)
  }
}

// Call on component mount
useEffect(() => {
  const init = async () => {
    setLoading(true)
    await fetchServers()
    await initSession()
    setLoading(false)
  }
  init()
}, [])
```

### Discover Tools for a Server
**File:** `frontend/src/App.jsx`
```javascript
const fetchTools = async (serverId) => {
  if (!session) return
  
  try {
    // Send tools/list request to gateway
    const res = await fetch(
      `${API_BASE}/execute?server=${encodeURIComponent(serverId)}`,
      {
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
      }
    )
    
    if (res.ok) {
      const data = await res.json()
      // Extract tools from nested result structure
      const toolList = data.result?.tools || data.result?.result?.tools || []
      setTools(prev => ({ ...prev, [serverId]: toolList }))
      console.log(`Loaded ${toolList.length} tools for ${serverId}`)
    } else {
      console.error(`Failed to fetch tools for ${serverId}:`, res.statusText)
    }
  } catch (err) {
    console.error('Failed to fetch tools:', err)
  }
}
```

### Execute a Tool with Parameters
**File:** `frontend/src/App.jsx`
```javascript
const executeTool = async () => {
  if (!session || !selectedTool) return
  setExecuting(true)
  
  const args = {}
  
  // Build arguments from form inputs
  if (selectedTool.inputSchema?.properties) {
    Object.keys(selectedTool.inputSchema.properties).forEach(param => {
      const paramConfig = selectedTool.inputSchema.properties[param]
      let value = paramValues[param]
      
      if (value !== undefined && value !== '') {
        // Type coercion
        if (paramConfig.type === 'integer' || paramConfig.type === 'number') {
          args[param] = Number(value)
        } else {
          args[param] = value
        }
      } else if (paramConfig.default !== undefined) {
        args[param] = paramConfig.default
      }
    })
  }
  
  try {
    const res = await fetch(
      `${API_BASE}/execute?server=${encodeURIComponent(selectedTool.server)}`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Session-ID': session.sessionId
        },
        body: JSON.stringify({
          jsonrpc: '2.0',
          method: 'tools/call',
          params: { name: selectedTool.tool, arguments: args },
          id: Date.now()
        })
      }
    )
    
    const data = await res.json()
    const resultKey = `${selectedTool.server}:${selectedTool.tool}`
    
    // Store result with timestamp
    setResults(prev => {
      const newResults = { ...prev }
      if (!newResults[resultKey]) {
        newResults[resultKey] = []
      }
      newResults[resultKey] = [...newResults[resultKey], {
        timestamp: new Date().toISOString(),
        request: args,
        response: data.result || data.error || data
      }]
      return newResults
    })
    
    console.log('Tool executed:', resultKey, data)
  } catch (err) {
    console.error('Tool execution failed:', err)
    setResults(prev => ({
      ...prev,
      [`${selectedTool.server}:${selectedTool.tool}`]: [{ error: err.message }]
    }))
  }
  
  setExecuting(false)
}
```

### Display Server Status
**File:** `frontend/src/App.jsx`
```javascript
const getStatusColor = (status) => {
  return status === 'connected' ? '#4ade80' : '#ef4444'
}

// In render:
{servers.map(server => (
  <div key={server.id} className="server-item">
    <div className="server-row">
      {/* Status indicator: filled circle if connected, empty if disconnected */}
      <span className="status-icon" style={{ color: getStatusColor(server.status) }}>
        {server.status === 'connected' ? '●' : '○'}
      </span>
      <span className="server-name">{server.id}</span>
      <span className="server-transport">[{server.transport}]</span>
    </div>
  </div>
))}

// Auto-refresh every 5 seconds to update status
useEffect(() => {
  const interval = setInterval(() => {
    fetchServers()  // Updates status indicators
  }, 5000)
  return () => clearInterval(interval)
}, [])
```

---

## 6. Agent Integration (Chatto)

### Chatto Client Tool Discovery
**File:** `agents/chatto/chatto_client.py`
```python
def discover_tools(self) -> Dict[str, List[Any]]:
    """Discover all tools from all registered servers"""
    if not self.session_id:
        self.init_session()

    servers_tools: Dict[str, List[Any]] = {}
    
    try:
        # 1. Get list of servers
        response = self.session.get(f"{self.mcp_gateway_url}/registry/servers")
        if not response.ok:
            return {}

        servers = response.json()

        # 2. Get tools for each server
        for server in servers:
            server_id = server.get("id")
            try:
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
                    # Extract tools - handle nested result structure
                    tools = data.get("result", {}).get("result", {}).get("tools", [])
                    servers_tools[server_id] = tools
                    
            except Exception as e:
                print(f"Error getting tools for {server_id}: {e}")
                servers_tools[server_id] = []

        return servers_tools
        
    except Exception as e:
        print(f"Error discovering tools: {e}")
    
    return {}
```

### Chatto Tool Execution
**File:** `agents/chatto/chatto_client.py`
```python
def execute_tool(
    self, server_name: str, tool_name: str, arguments: Dict[str, Any]
) -> dict[str, Any]:
    """Execute an MCP tool and extract text result"""
    
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
            
            # Extract result from nested structure
            result = data.get("result", {}).get("result", {})
            content = result.get("content", [])

            # Extract text from MCP response
            if content and isinstance(content, list):
                text_content = content[0].get("text", "")
                return {"content": text_content}

            return {"raw_result": str(data)}
        
        return {"error": response.text}
        
    except Exception as e:
        return {"error": str(e)}
```

---

## 7. Error Handling Best Practices

### Try-Catch Patterns
```python
# Pattern 1: Transport-level error handling
try:
    response = await client.post(url, json=data)
    response.raise_for_status()
    return response.json()
except httpx.HTTPStatusError as e:
    return {"error": {"code": -32603, "message": f"HTTP error: {e.status_code}"}}
except httpx.TimeoutException:
    return {"error": {"code": -32603, "message": "Request timeout"}}
except Exception as e:
    return {"error": {"code": -32603, "message": str(e)}}

# Pattern 2: Session validation
try:
    get_session(x_session_id)
except HTTPException as e:
    raise HTTPException(status_code=404, detail="Invalid session")

# Pattern 3: Configuration loading
try:
    config = load_config()
except FileNotFoundError:
    raise HTTPException(status_code=404, detail="Config not found")
except json.JSONDecodeError as e:
    raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
```

### Logging Patterns
```python
# Prefix-based logging for different components
print(f"[MCP] Main message handling")
print(f"[NPX] Process initialization")
print(f"[Discovery] Docker server discovery")
print(f"[Registry] External registry operations")
print(f"[SSE] Server-sent events")

# Error logging with context
print(f"[MCP] Error handling message for {server_name}: {e}")
print(f"[Registry] Error fetching servers: {e}")
```

---

## 8. Testing Skills-Provider Connection

### Manual Request to Skills-Provider
```bash
# Create session
SESSION=$(curl -X POST http://localhost:8000/sessions | jq -r '.sessionId')

# Discover tools
curl -X POST http://localhost:8000/execute?server=skills-provider \
  -H "X-Session-ID: $SESSION" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/list",
    "params": {},
    "id": 1
  }'

# Execute tool
curl -X POST http://localhost:8000/execute?server=skills-provider \
  -H "X-Session-ID: $SESSION" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "get_skills",
      "arguments": {"category": "general"}
    },
    "id": 2
  }'
```

### Docker Compose Test
```yaml
# compose_mcp_servers_test.yml
version: '3.8'
services:
  mcp-gateway:
    build: .
    ports:
      - "8000:8000"
    environment:
      - HOST=0.0.0.0
      - PORT=8000

  skills-provider:
    image: skills-provider:latest
    ports:
      - "3001:3001"
    environment:
      - MCP_PORT=3001
```

```bash
# Start all services
docker-compose -f compose_mcp_servers_test.yml up -d

# Check logs
docker-compose -f compose_mcp_servers_test.yml logs -f mcp-gateway
```
