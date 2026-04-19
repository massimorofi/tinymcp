#!/bin/bash
#
# start.sh - Start the MCP cluster (tinymcp gateway + skillsmcp + Docker MCP servers)
#
# Usage: ./start.sh
#        COMPOSE_FILE=/path/to/compose_docker.yml ./start.sh
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

# ─── Pre-flight checks ─────────────────────────────────────

check_dependencies() {
    local missing=()
    for cmd in docker docker compose; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Missing dependencies: ${missing[*]}"
        error "Please install Docker and Docker Compose."
        exit 1
    fi
    info "Docker and Docker Compose are available."
}

# ─── Prepare environment ───────────────────────────────────

prepare_env() {
    # Create .env from .env.example if .env doesn't exist
    if [[ -f "${SCRIPT_DIR}/.env.example" ]] && [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
        info "Creating .env from .env.example"
        cp "${SCRIPT_DIR}/.env.example" "${SCRIPT_DIR}/.env"
    fi

    # Ensure config directories exist
    mkdir -p "${SCRIPT_DIR}/config/tinymcp"
    mkdir -p "${SCRIPT_DIR}/config/skillsmcp"
    mkdir -p "${SCRIPT_DIR}/skills"

    # Ensure config files exist
    if [[ ! -f "${SCRIPT_DIR}/config/tinymcp/config.json" ]]; then
        warn "config/tinymcp/config.json not found. Creating default..."
        cat > "${SCRIPT_DIR}/config/tinymcp/config.json" <<'EOF'
{
  "mcpServers": {
    "skills-provider": {
      "transport": "streamable-http",
      "url": "http://skillsmcp:3001/mcp"
    }
  }
}
EOF
    fi

    if [[ ! -f "${SCRIPT_DIR}/config/tinymcp/secrets.json" ]]; then
        echo '{"mcpServers": {}}' > "${SCRIPT_DIR}/config/tinymcp/secrets.json"
    fi

    if [[ ! -f "${SCRIPT_DIR}/config/skillsmcp/skills.settings.json" ]]; then
        warn "config/skillsmcp/skills.settings.json not found. Creating default..."
        cat > "${SCRIPT_DIR}/config/skillsmcp/skills.settings.json" <<'EOF'
{
  "directories": ["/home/user/.claude/skills"],
  "reload": false,
  "supporting_files": "template",
  "http": {
    "enabled": true,
    "port": 3001,
    "host": "0.0.0.0",
    "path": "/mcp"
  },
  "gateway": {
    "enabled": false,
    "host": "localhost",
    "port": 8000,
    "name": "skills-provider",
    "transport": "streamable-http"
  }
}
EOF
    fi

    ok "Environment prepared."
}

# ─── Start the cluster ─────────────────────────────────────

start_cluster() {
    info "Starting MCP cluster..."
    info "Compose file: ${COMPOSE_FILE}"
    info "Project name: ${COMPOSE_PROJECT_NAME}"
    echo ""

    docker compose -f "${COMPOSE_FILE}" -p "${COMPOSE_PROJECT_NAME}" up -d --build

    echo ""
    info "Waiting for services to become healthy..."
    sleep 5

    # Check health of the gateway
    local retries=10
    local gateway_healthy=false
    while [[ $retries -gt 0 ]]; do
        if curl -sf "http://localhost:8080/healthz" &>/dev/null; then
            gateway_healthy=true
            break
        fi
        retries=$((retries - 1))
        sleep 2
    done

    echo ""
    if [[ "$gateway_healthy" == "true" ]]; then
        ok "MCP Gateway is running at http://localhost:8080"
    else
        warn "MCP Gateway may not be fully ready yet. Check with:"
        warn "  docker compose -f ${COMPOSE_FILE} -p ${COMPOSE_PROJECT_NAME} ps"
    fi

    echo ""
    info "Services started:"
    docker compose -f "${COMPOSE_FILE}" -p "${COMPOSE_PROJECT_NAME}" ps

    echo ""
    info "To stop the cluster, run:"
    info "  ./stop.sh"
    echo ""
    info "MCP client configuration:"
    echo "  Add the following to your MCP client config:"
    echo ""
    echo "  {"
    echo "    \"mcpServers\": {"
    echo "      \"gateway\": {"
    echo "        \"transport\": \"streamable-http\","
    echo "        \"url\": \"http://localhost:8080/mcp\""
    echo "      }"
    echo "    }"
    echo "  }"
    echo ""
}

# ─── Main ───────────────────────────────────────────────────

main() {
    info "Starting MCP Cluster"
    echo "========================"
    echo ""

    check_dependencies
    prepare_env
    start_cluster
}

main "$@"
