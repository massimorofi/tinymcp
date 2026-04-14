import json
from typing import Any, AsyncGenerator, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from mcp.server.sse import SseServerTransport

from config import load_config
from execution import handle_message


async def handle_messages_endpoint(request: Request) -> JSONResponse:
    """Handle incoming messages from clients."""
    config = load_config()
    
    try:
        data: dict[str, Any] = await request.json()
        if "id" not in data:
            raise HTTPException(status_code=400, detail="Missing 'id' field")

        result = await handle_message(data, None)

        return JSONResponse(content={"result": result})
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Internal error: {str(e)}"},
        )


async def handle_sse_endpoint(request: Request) -> StreamingResponse:
    """Handle SSE connections."""
    config = load_config()
    
    if not config.get("mcpServers"):
        raise HTTPException(status_code=500, detail="No MCP servers configured")
    
    # Find the first MCP server name for heartbeat
    mcp_server_name = None
    for server_id in config.get("mcpServers", {}).keys():
        if server_id:
            mcp_server_name = server_id
            break
    
    sse = SseServerTransport("/messages")
    
    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async with sse.connect_sse(request.url.path):
                await sse.handle_post_message({"method": "initialize"})
                
                # Send heartbeat to gateway when connection established
                if mcp_server_name:
                    try:
                        import urllib.request
                        heartbeat_url = request.url.replace(path="/heartbeat", query=f"server={mcp_server_name}")
                        req = urllib.request.Request(str(heartbeat_url), method='GET')
                        urllib.request.urlopen(req, timeout=5)
                    except Exception:
                        pass  # Ignore heartbeat failures
                
                yield "data: {\"status\": \"connected\"}\n\n"
        except Exception as e:
            print(f"[SSE] Connection error: {e}")
            raise
    
    return StreamingResponse(event_stream(), media_type="text/event-stream")
