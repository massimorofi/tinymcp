# Minimal MCP Gateway

A lightweight Gateway that acts as an aggregator for multiple MCP Servers. It provides a unified SSE endpoint for clients to connect, discover tools, and execute resources.

## Introduction

The MCP Gateway bridges external clients to MCP servers via Server-Sent Events (SSE), enabling a unified interface to multiple MCP server instances.

## Prerequisites

- Python 3.10+
- Docker & Docker Compose
- `uvx` or `pip` (for local development)

## Setup

```bash
./scripts/install.sh
```

## Running

### Option 1: Docker Compose (Recommended)

Start all services including MCP servers:

```bash
docker-compose -f compose_mcp_servers_test.yml up -d --build
```

Check logs:
```bash
docker-compose -f compose_mcp_servers_test.yml logs -f mcp-gateway
```

Stop services:
```bash
docker-compose -f compose_mcp_servers_test.yml down
```

### Option 2: Local Development

```bash
./scripts/start.sh
```

## Configuration

Add new servers to `config.json`:

```json
{
  "mcpServers": {
    "server-name": {
      "transport": "stdio",
      "command": "uvx",
      "args": ["mcp-server-example"]
    }
  }
}
```

### Transport Types

- **stdio** - Local process-based MCP server
- **sse** - Remote SSE-based MCP server
- **docker-stdio** - MCP server running in Docker container

Example for docker-stdio:
```json
{
  "mcpServers": {
    "desktop-commander": {
      "transport": "docker-stdio",
      "container": "tinymcp-desktop-commander-1"
    }
  }
}
```

## Access

**Local:** `http://localhost:8080`

**Docker:** `http://localhost:8080`

| Endpoint | Description |
|----------|-------------|
| `http://localhost:8080` | Gateway info |
| `http://localhost:8080/healthz` | Health check |
| `http://localhost:8080/docs` | Swagger API docs |
| `http://localhost:8080/registry/servers` | List servers |

## Architecture

```
Client <-> SSE Gateway <-> [MCP Server A, MCP Server B]
```

### Environment Variables

- `HOST`: Bind address (default: `0.0.0.0`)
- `PORT`: Port to listen on (default: `8080`)
- `SECRETS_PATH`: Path to secrets file (default: `./secrets.json`)

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check with gateway info |
| `/healthz` | GET | Simple health check |
| `/registry/servers` | GET | List all registered MCP servers |
| `/registry/servers` | POST | Register a new MCP server (stdio or SSE) |
| `/sessions` | POST | Initialize a unified MCP session |
| `/execute` | POST | Execute a tool (requires X-Session-ID header) |
| `/sse` | GET | SSE connection endpoint |
| `/messages` | POST | Handle client-to-server messages |

### Server Registration

Register a new stdio server:
```bash
curl -X POST http://localhost:8080/registry/servers \
  -H "Content-Type: application/json" \
  -d '{"id": "github-tools", "transport": "stdio", "command": "uvx", "args": ["mcp-server-github"]}'
```

Register a new SSE server:
```bash
curl -X POST http://localhost:8080/registry/servers \
  -H "Content-Type: application/json" \
  -d '{"id": "remote-server", "transport": "sse", "url": "https://example.com/mcp"}'
```

Register a Docker-based server:
```bash
curl -X POST http://localhost:8080/registry/servers \
  -H "Content-Type: application/json" \
  -d '{"id": "desktop-commander", "transport": "docker-stdio", "container": "tinymcp-desktop-commander-1"}'
```

### Session Management

Initialize a session:
```bash
curl -X POST http://localhost:8080/sessions
```

Response:
```json
{
  "sessionId": "uuid-of-session",
  "mcpVersion": "1.0.0"
}
```

### Tool Execution

Execute a tool with session:
```bash
curl -X POST http://localhost:8080/execute \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: <session-id>" \
  -d '{"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "tool_name"}, "id": 1}'
```

## Testing

```bash
./scripts/run_tests.sh
```

## Troubleshooting

Check container status:
```bash
docker ps
```

View gateway logs:
```bash
docker logs mcp-gateway
```

Restart gateway:
```bash
docker-compose -f compose_mcp_servers_test.yml restart mcp-gateway
```