#!/bin/sh
# Writes .env with generated database credentials, once. Run before the
# first `docker compose up`. An existing .env is left alone.
set -eu
cd "$(dirname "$0")/.."
if [ -e .env ]; then
    echo ".env already exists; leaving it alone"
    exit 0
fi
password=$(openssl rand -hex 24)
umask 077
cat > .env <<ENV
POSTGRES_USER=classroom
POSTGRES_DB=classroom
POSTGRES_PASSWORD=$password
DATABASE_URL=postgresql://classroom:$password@db:5432/classroom
ENV
echo "wrote .env"
