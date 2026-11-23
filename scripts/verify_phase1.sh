#!/usr/bin/env bash
# Checks a running Phase 1 stack (after `docker compose up -d --build`) and prints PASS/FAIL per check.
# Run from the repo root, in Git Bash on Windows or any Linux/macOS shell: bash scripts/verify_phase1.sh

set -u
PASS=0
FAIL=0

check() {  # check "<description>" <command...>
  local name="$1"; shift
  if "$@" > /dev/null 2>&1; then
    echo "PASS  $name"; PASS=$((PASS + 1))
  else
    echo "FAIL  $name"; FAIL=$((FAIL + 1))
  fi
}

healthy() {  # healthy <service>: the container reports health "healthy"
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q "$1")")" = "healthy" ]
}

running() {
  [ "$(docker inspect -f '{{.State.Status}}' "$(docker compose ps -q "$1")")" = "running" ]
}

if [ ! -f .env ]; then
  echo "No .env found. Run: cp .env.example .env  (then set POSTGRES_PASSWORD)"; exit 1
fi
DB_USER=$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-); DB_USER=${DB_USER:-bharat}
DB_NAME=$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2-); DB_NAME=${DB_NAME:-bharatagent}

echo "== Containers"
for s in db ollama mcp-runner api; do check "$s is healthy" healthy "$s"; done
check "web is running" running web

echo "== API"
check "GET :8000/health returns ok" sh -c "curl -sf http://127.0.0.1:8000/health | grep -q '\"ok\":true'"
check "GET :8000/status shows db up" sh -c "curl -sf http://127.0.0.1:8000/status | grep -q '\"db\":true'"
check "GET :8000/status shows ollama up" sh -c "curl -sf http://127.0.0.1:8000/status | grep -q '\"ollama\":true'"
check "GET :8000/status shows mcp_runner up" sh -c "curl -sf http://127.0.0.1:8000/status | grep -q '\"mcp_runner\":true'"

echo "== Web"
check "web serves the React page on :3000" sh -c "curl -sf http://127.0.0.1:3000/ | grep -q 'id=\"root\"'"
check "web proxies /api/status to the api" sh -c "curl -sf http://127.0.0.1:3000/api/status | grep -q '\"api\":true'"

echo "== Database"
check "pgvector extension installed" sh -c \
  "docker compose exec -T db psql -U '$DB_USER' -d '$DB_NAME' -tAc \"SELECT extversion FROM pg_extension WHERE extname='vector'\" | grep -q '[0-9]'"
check "tables users, graphs, chunks, usage exist" sh -c \
  "[ \"\$(docker compose exec -T db psql -U '$DB_USER' -d '$DB_NAME' -tAc \"SELECT count(*) FROM information_schema.tables WHERE table_name IN ('users','graphs','chunks','usage')\")\" = 4 ]"
check "db publishes no ports to the host" sh -c \
  "id=\$(docker compose ps -q db) && [ -n \"\$id\" ] && [ -z \"\$(docker port \"\$id\")\" ]"

echo "== Network isolation"
# Must print BLOCKED from inside the container; a failed `docker compose exec` is not proof of isolation.
check "mcp-runner CANNOT reach db:5432" sh -c \
  "docker compose exec -T mcp-runner python -c \"
import socket
try:
    socket.create_connection(('db', 5432), 3)
    print('REACHABLE')
except OSError:
    print('BLOCKED')
\" | grep -q BLOCKED"
check "api CAN reach db:5432" \
  docker compose exec -T api python -c "import socket; socket.create_connection(('db', 5432), 3)"
check "api CAN reach mcp-runner:8100" \
  docker compose exec -T api python -c "import socket; socket.create_connection(('mcp-runner', 8100), 3)"

echo "== MCP"
check "ping tool returns pong" sh -c "docker compose exec -T mcp-runner python -c \"
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
async def main():
    async with streamablehttp_client('http://localhost:8100/mcp') as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool('ping', {})
            assert res.content[0].text == 'pong'
asyncio.run(main())
\""

echo "== Ollama"
check "ollama CLI responds" docker compose exec -T ollama ollama --version
check "ollama API answers on :11434" sh -c "curl -sf http://127.0.0.1:11434/ | grep -qi 'ollama is running'"

echo "== Secrets"
check ".env is ignored by git" git check-ignore -q .env
check ".env is not tracked by git" sh -c "! git ls-files --error-unmatch .env"

echo
echo "Result: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
