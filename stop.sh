#!/bin/bash
#
# stop.sh - Stop the MCP cluster (tinymcp gateway + skillsmcp + Docker MCP servers)
#
# Usage: ./stop.sh
#        COMPOSE_FILE=/path/to/compose_docker.yml ./stop.sh
#        ./stop.sh --remove-images  # Also remove built images
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${SCRIPT_DIR}/compose_docker.yml}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-mcp-cluster}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ─── Parse arguments ───────────────────────────────────────

REMOVE_IMAGES=false
for arg in "$@"; do
    case "$arg" in
        --remove-images|-r)
            REMOVE_IMAGES=true
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --remove-images, -r   Also remove built images"
            echo "  --help, -h            Show this help message"
            exit 0
            ;;
        *)
            warn "Unknown argument: $arg"
            ;;
    esac
done

# ─── Stop the cluster ──────────────────────────────────────

stop_cluster() {
    # Check if any containers are running
    local running
    running=$(docker compose -f "${COMPOSE_FILE}" -p "${COMPOSE_PROJECT_NAME}" ps --format name 2>/dev/null | head -1)

    if [[ -z "$running" ]]; then
        warn "No MCP cluster containers are running."
        exit 0
    fi

    info "Stopping MCP cluster..."
    docker compose -f "${COMPOSE_FILE}" -p "${COMPOSE_PROJECT_NAME}" down

    ok "MCP cluster stopped."

    echo ""
    info "Services stopped:"
    docker compose -f "${COMPOSE_FILE}" -p "${COMPOSE_PROJECT_NAME}" ps || true

    # Remove images if requested
    if [[ "$REMOVE_IMAGES" == "true" ]]; then
        echo ""
        info "Removing built images..."
        docker compose -f "${COMPOSE_FILE}" -p "${COMPOSE_PROJECT_NAME}" down --rmi all
        ok "Built images removed."
    fi

    echo ""
    info "To start the cluster again, run:"
    info "  ./start.sh"
}

# ─── Main ───────────────────────────────────────────────────

main() {
    info "Stopping MCP Cluster"
    echo "========================"
    echo ""

    stop_cluster
}

main "$@"
