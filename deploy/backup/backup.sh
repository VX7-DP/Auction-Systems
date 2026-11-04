#!/bin/sh
# Postgres dumps for the one-VM deploy (docs/deploy.md). Runs in the `backup` service.
#
#   backup now     take a dump now, then delete dumps older than BACKUP_KEEP_DAYS
#   backup daily   take one at once if none is under a day old, then one every day at
#                  BACKUP_TIME (UTC). This is the service's command.
#
# The connection comes from the standard PG* variables (PGHOST, PGUSER, PGPASSWORD,
# PGDATABASE). Dumps are written to /backups as auction-<UTC time>.dump, in pg_dump's
# custom format. Restore one with deploy/restore.sh.

dir=/backups
keep_days=${BACKUP_KEEP_DAYS:-14}
at=${BACKUP_TIME:-03:15}

log() {
	echo "backup: $*"
}

# Errors are checked by hand: `set -e` is ignored inside a function called with ||.
dump() {
	umask 077
	name="auction-$(date -u +%Y%m%dT%H%M%SZ).dump"
	partial="$dir/.$name.partial"
	rm -f "$dir"/.auction-*.partial
	# Write under a temporary name, so a failed dump never looks like a good one.
	if ! pg_dump --format=custom --file="$partial"; then
		rm -f "$partial"
		log "FAILED: pg_dump exited non-zero" >&2
		return 1
	fi
	mv "$partial" "$dir/$name" || return 1
	log "wrote $name ($(du -h "$dir/$name" | cut -f1))"
	# Prune only after a good dump, so the newest good one is always kept.
	find "$dir" -maxdepth 1 -name 'auction-*.dump' -mtime +"$keep_days" -print -exec rm -f {} + |
		sed 's/^/backup: deleted old /'
}

seconds_until_next_run() {
	now=$(date -u +%s)
	next=$(date -u -d "$(date -u +%Y-%m-%d) $at" +%s) || return 1
	[ "$next" -gt "$now" ] || next=$((next + 86400))
	echo $((next - now))
}

case "${1:-}" in
now)
	dump
	;;
daily)
	seconds_until_next_run >/dev/null || {
		log "BACKUP_TIME must be HH:MM (UTC), got '$at'" >&2
		exit 2
	}
	if [ -z "$(find "$dir" -maxdepth 1 -name 'auction-*.dump' -mmin -1440)" ]; then
		dump || log "will retry at $at UTC" >&2
	fi
	while :; do
		wait_s=$(seconds_until_next_run)
		log "next dump at $at UTC, in ${wait_s}s"
		sleep "$wait_s"
		dump || log "will retry at $at UTC" >&2
	done
	;;
*)
	echo "usage: backup now|daily" >&2
	exit 2
	;;
esac
