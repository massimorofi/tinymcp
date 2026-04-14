#!/bin/bash
# Start the MCP Gateway
./scripts/start.sh > ./logs/gateway.log 2>&1 &
# Start Frontend
./scripts/start_frontend.sh &
docker ps


