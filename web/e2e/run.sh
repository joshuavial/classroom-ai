#!/bin/sh
# Runs the Playwright smoke against a fresh compose stack, then removes it.
# A fake model server runs on the host behind the real worker agent, so the
# smoke covers join, heartbeat, routing and streaming without a real model.
# The guard is off for this run only (GUARD_DISABLED=1), until step 6.
# HTTP_PORT picks the host port (default 8080). Browsers live in node_modules.
set -eu
cd "$(dirname "$0")/../.."
root=$(pwd)
port=${HTTP_PORT:-8080}
model_port=${FAKE_MODEL_PORT:-18092}
export HTTP_PORT=$port GUARD_DISABLED=1 COMPOSE_PROJECT_NAME=cai-e2e-server
[ -e .env ] || ./scripts/init-env.sh
fake_pid=""
cleanup() {
    [ -n "$fake_pid" ] && kill "$fake_pid" 2>/dev/null || true
    # The worker compose file insists on these even to stop it.
    SERVER_URL=unused JOIN_TOKEN=unused COMPOSE_PROJECT_NAME=cai-e2e-worker \
        docker compose -f worker/compose.yml down -v >/dev/null 2>&1 || true
    docker compose down -v >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker compose down -v >/dev/null 2>&1 || true
uv run python -m tests.fake_model_server --port "$model_port" &
fake_pid=$!
docker compose up -d --build
code=""
for _ in $(seq 1 60); do
    code=$(docker compose logs app 2>/dev/null | sed -n 's/.*setup code: \([A-Z0-9-]*\).*/\1/p' | tail -1)
    [ -n "$code" ] && curl -fs "http://localhost:$port/healthz" >/dev/null && break
    sleep 1
done
[ -n "$code" ] || { echo "no setup code in the app log" >&2; exit 1; }
cd web
REPO_ROOT="$root" BASE_URL="http://localhost:$port" SETUP_CODE="$code" \
    BACKEND_URL="http://host.docker.internal:$model_port" \
    PLAYWRIGHT_BROWSERS_PATH=0 npx playwright test "$@"
