#!/bin/bash
pids=$(pgrep -f "uvicorn main:app")
if [ -z "$pids" ]; then
  echo "MCP Gateway is not running."
else
  for pid in $pids; do
    kill "$pid" 2>/dev/null || true
  done
  echo "MCP Gateway stopped."
fi