#!/bin/bash
# Stop the frontend dev server
pkill -f "vite" 2>/dev/null || true
echo "Frontend stopped"