#!/bin/sh
# Writes .env with generated database credentials, once. Run before the
# first `docker compose up`. An existing .env is left alone.
#
#   scripts/init-env.sh                 development: http://localhost only
#   scripts/init-env.sh <server name>   a school server: HTTPS for that name
#                                       (a local DNS name or an IP address),
#                                       reachable from the network
set -eu
cd "$(dirname "$0")/.."
if [ -e .env ]; then
    echo ".env already exists; leaving it alone"
    exit 0
fi
server_name=${1:-}
case $server_name in
    *[!A-Za-z0-9.-]*) echo "server name may only hold letters, digits, dots and dashes" >&2; exit 2 ;;
esac
password=$(openssl rand -hex 24)
umask 077
cat > .env <<ENV
POSTGRES_USER=classroom
POSTGRES_DB=classroom
POSTGRES_PASSWORD=$password
DATABASE_URL=postgresql://classroom:$password@db:5432/classroom
ENV
if [ -n "$server_name" ]; then
    printf 'SERVER_NAME=%s\nBIND_ADDRESS=0.0.0.0\nCOMPOSE_FILE=compose.yml:compose.https.yml\n' "$server_name" >> .env
fi
echo "wrote .env"
