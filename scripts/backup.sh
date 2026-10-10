#!/bin/sh
# Backs up the whole database (settings, accounts, conversations) to a file,
# while the stack keeps running. Usage: scripts/backup.sh [file]
# The file holds student conversations: keep it somewhere only staff can read.
set -eu
out=${1:-classroom-ai-$(date +%Y%m%d-%H%M%S).dump}
case $out in /*) ;; *) out="$PWD/$out" ;; esac
cd "$(dirname "$0")/.."
umask 077
tmp="$out.partial"
trap 'rm -f "$tmp"' EXIT
docker compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$tmp"
if [ ! -s "$tmp" ]; then
    echo "backup came out empty; nothing written" >&2
    exit 1
fi
mv -f "$tmp" "$out"
trap - EXIT
echo "wrote $out"
