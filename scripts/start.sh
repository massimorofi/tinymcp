#!/bin/bash
source venv/bin/activate
export HOST=${HOST:-"0.0.0.0"}
export PORT=${PORT:-8000}
echo "Starting MCP Gateway on $HOST:$PORT..."
exec uvicorn main:app --host $HOST --port $PORT
