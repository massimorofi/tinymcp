This specification is designed for **Claude Code** (or any AI coding agent) to scaffold a functional, minimal **MCP Gateway** using Python. 

The gateway will use **FastAPI** and the official **MCP Python SDK** to bridge external clients to MCP servers via Server-Sent Events (SSE).

---

# Specification: Minimal MCP Gateway (Python)

## 1. Project Overview
Build a lightweight Gateway that acts as an aggregator for multiple MCP Servers. It should provide a unified SSE endpoint for clients to connect, discover tools, and execute resources across the configured server mesh.

### Tech Stack
* **Language:** Python 3.10+
* **Framework:** FastAPI / Uvicorn
* **MCP Library:** `mcp[cli]`
* **Task Runner:** Bash scripts

---

## 2. Directory Structure
```text
mcp-gateway/
├── .env                # Environment variables
├── config.json         # MCP Server configurations
├── requirements.txt    # Dependencies
├── main.py             # FastAPI Application
├── scripts/
│   ├── install.sh      # Setup script
│   ├── start.sh        # Run script
│   └── stop.sh         # Shutdown script
└── README.md           # Documentation
```

---

## 3. File Specifications

### A. Dependencies (`requirements.txt`)
```text
mcp>=0.1.0
fastapi
uvicorn
python-dotenv
httpx
```

### B. Core Gateway (`main.py`)
**Requirements for Claude Code:**
* Implement a FastAPI app.
* Load server configurations from `config.json`.
* Initialize an `SseServerTransport` for client communication.
* Implement an endpoint `/sse` for the initial connection.
* Implement an endpoint `/messages` to handle client-to-server posts.
* Ensure the gateway can proxy `list_tools` and `call_tool` requests to the configured MCP servers.

### C. Configuration (`config.json`)
```json
{
  "mcpServers": {
    "sqlite": {
      "command": "uvx",
      "args": ["mcp-server-sqlite", "--db-path", "./test.db"]
    }
  }
}
```

---

## 4. Automation Scripts

### Install Script (`scripts/install.sh`)
```bash
#!/bin/bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
echo "Installation complete. Configure config.json before starting."
```

### Start Script (`scripts/start.sh`)
```bash
#!/bin/bash
source venv/bin/activate
export HOST=${HOST:-"0.0.0.0"}
export PORT=${PORT:-8000}
echo "Starting MCP Gateway on $HOST:$PORT..."
exec uvicorn main:app --host $HOST --port $PORT
```

### Stop Script (`scripts/stop.sh`)
```bash
#!/bin/bash
# Find and kill the uvicorn process associated with main:app
pid=$(pgrep -f "uvicorn main:app")
if [ -z "$pid" ]; then
  echo "MCP Gateway is not running."
else
  kill $pid
  echo "MCP Gateway (PID $pid) stopped."
fi
```

---

## 5. Documentation (`README.md`)
**Requirements for Claude Code:**
Create a README that includes:
1.  **Introduction:** Brief explanation of the MCP Gateway.
2.  **Prerequisites:** Python 3.10+, `uvx` or `pip`.
3.  **Setup:** Instructions to run `./scripts/install.sh`.
4.  **Configuration:** How to add new servers to `config.json`.
5.  **Running:** Instructions to use `./scripts/start.sh`.
6.  **Architecture Diagram:** A simple text-based flow: 
    `Client <-> SSE Gateway <-> [MCP Server A, MCP Server B]`.

---

## Instructions for Claude Code Execution
> "Claude, please initialize this project in the current directory. Follow the structure provided. Ensure `main.py` uses asynchronous handlers for MCP transport. When creating `main.py`, include robust error handling for cases where a configured MCP server fails to start or respond. Make all scripts executable."

