# Nightly Postgres dumps (the `backup` service in deploy/docker-compose.prod.yml).
# Same image as the database, so pg_dump matches the server version.
FROM postgres:16-alpine
COPY --chmod=755 backup.sh /usr/local/bin/backup
