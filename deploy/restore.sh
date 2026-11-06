#!/bin/sh
# Replace the live database with a dump taken by the `backup` service (docs/deploy.md).
#
#   deploy/restore.sh auction-20261105T031500Z.dump
#
# The argument is a file name in the backup directory (BACKUP_DIR in .env). Every bid
# confirmed after that dump was taken is lost. First the script takes a safety dump of
# the current database, so the restore can be undone. SKIP_SAFETY_DUMP=1 skips it, for
# when the current database can't be dumped. RESTORE_SEQ_JUMP (default 1000000) is how far
# every auction's sequence numbers move forward; see the step after pg_restore.
set -eu
cd "$(dirname "$0")/.."

# stdin from /dev/null: docker compose exec would otherwise eat the typed confirmation.
compose() {
	docker compose "$@" </dev/null
}

seq_jump=${RESTORE_SEQ_JUMP:-1000000}
case "$seq_jump" in
"" | *[!0-9]*)
	echo "RESTORE_SEQ_JUMP must be a whole number, got '$seq_jump'" >&2
	exit 2
	;;
esac

file=${1:-}
case "$file" in
"" | */* | .*)
	echo "usage: deploy/restore.sh <dump file name from the backup directory>" >&2
	exit 2
	;;
esac
compose exec -T backup test -f "/backups/$file" || {
	echo "No /backups/$file in the backup service. Available dumps:" >&2
	compose exec -T backup ls -1 /backups >&2
	exit 1
}

printf 'This replaces the live database with %s.\nType "restore" to go on: ' "$file"
answer=
read -r answer || true
[ "$answer" = restore ] || {
	echo "Stopped. Nothing changed."
	exit 1
}

if [ "${SKIP_SAFETY_DUMP:-0}" != 1 ]; then
	echo "Safety dump of the current database..."
	compose exec -T backup backup now
fi

echo "Stopping the backend, so nothing writes during the restore..."
compose stop backend

echo "Restoring $file..."
compose exec -T backup dropdb --if-exists --force --maintenance-db=postgres auction
compose exec -T backup createdb --maintenance-db=postgres auction
compose exec -T backup pg_restore --single-transaction --no-owner --dbname=auction "/backups/$file"

echo "Bringing the restored database up to the current schema..."
compose run --rm migrate

# Open pages ignore any sequence number at or below the one they already show. The dump
# holds older seq ceilings than the ones Redis used after it was taken, so without this
# step seqs would resume below what viewers saw, and their pages would stop updating
# until bidding caught up. Moving every ceiling past that makes each page see one gap
# and resync from the restored state, as after a Redis restart.
echo "Moving sequence numbers forward by $seq_jump, past anything viewers saw..."
compose exec -T backup psql --dbname=auction -v ON_ERROR_STOP=1 -c \
	"UPDATE auctions SET seq_ceiling = GREATEST(seq_ceiling, high_seq) + $seq_jump"

# Redis has no persistence, so a restart empties it. Each auction then reloads from the
# restored Postgres (the bid engine's NOT_LOADED path). Without this, Redis would keep
# seq values and bids newer than the restored data.
echo "Restarting Redis, so it rebuilds from the restored database..."
compose restart redis

echo "Starting the backend..."
compose up -d --wait
echo "Done. Restored $file."
