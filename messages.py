"""
Messages module - Handle MCP message endpoints.

Provides endpoints for:
- Processing incoming MCP messages
- Server-Sent Events (SSE) connections for streaming
"""

import json
import time
from typing import Any, AsyncGenerator, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from config import load_config
from execution import handle_message


async def handle_messages_endpoint(request: Request) -> JSONResponse:
    """
    Handle incoming messages from clients via POST /messages.

    Accepts JSON-RPC formatted messages and forwards them
    to the appropriate MCP server for processing.
    """
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
    """
    Handle SSE connections for streaming responses.

    This endpoint maintains SSE connections for clients.
    Actual tool calls should use the /execute endpoint with session IDs.
    """
    async def event_stream() -> AsyncGenerator[str, None]:
        import asyncio
        try:
            # Send initial connection confirmation
            yield 'data: {"status": "connected", "message": "SSE connection established"}\n\n'
            
            # Send periodic heartbeat to keep connection alive
            for i in range(300):  # Keep connection open for ~5 minutes
                await asyncio.sleep(15)
                timestamp = int(time.time())
                yield f'data: {{"status": "heartbeat", "timestamp": {timestamp}, "count": {i}}}\n\n'
        except asyncio.CancelledError:
            print("[SSE] Connection cancelled by client")
        except Exception as e:
            print(f"[SSE] Connection error: {e}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")



