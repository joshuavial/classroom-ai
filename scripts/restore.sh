#!/bin/sh
# Replaces everything in the database with a backup from scripts/backup.sh.
# Usage: scripts/restore.sh [--yes] <file>
# The app is stopped while it runs. The replacement is one transaction: if it
# fails, the database is left as it was.
set -eu
yes=
if [ "${1:-}" = "--yes" ]; then
    yes=1
    shift
fi
if [ $# -ne 1 ]; then
    echo "usage: scripts/restore.sh [--yes] <backup file>" >&2
    exit 2
fi
file=$1
case $file in /*) ;; *) file="$PWD/$file" ;; esac
if [ ! -r "$file" ] || [ ! -s "$file" ]; then
    echo "cannot read $file" >&2
    exit 1
fi
cd "$(dirname "$0")/.."

if ! docker compose exec -T db pg_restore --list < "$file" > /dev/null; then
    echo "$file is not a backup from scripts/backup.sh; nothing changed" >&2
    exit 1
fi
if [ -z "$yes" ]; then
    printf 'This replaces every account, class and conversation with the backup. Type yes to go on: '
    read -r answer
    if [ "$answer" != "yes" ]; then
        echo "nothing changed"
        exit 1
    fi
fi

docker compose stop app
# Inside the db container: unpack the dump to SQL, then drop and recreate the
# schema and load the SQL in one transaction.
if docker compose exec -T db sh -c '
    set -eu
    dir=$(mktemp -d)
    trap "rm -rf $dir" EXIT
    cat > "$dir/backup.dump"
    pg_restore --no-owner -f "$dir/backup.sql" "$dir/backup.dump"
    psql -X -q -o /dev/null -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        -c "SET client_min_messages TO warning" -c "DROP SCHEMA public CASCADE" \
        -c "CREATE SCHEMA public AUTHORIZATION pg_database_owner" -f "$dir/backup.sql"
' < "$file"; then
    status=0
    echo "restored $file"
else
    status=1
    echo "restore failed; the database is as it was before" >&2
fi
docker compose start app
# The app migrates on start and refuses a backup newer than its code.
i=0
until docker compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)" 2>/dev/null; do
    i=$((i + 1))
    if [ $i -ge "${HEALTH_TRIES:-30}" ]; then
        echo "the app has not come back; see: docker compose logs app" >&2
        exit 1
    fi
    sleep 2
done
echo "the app is up"
exit $status
